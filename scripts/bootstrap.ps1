[CmdletBinding()]
param(
    [string]$GitHubOwner,
    [string]$GitHubRepository,
    [string]$AzureSubscriptionId,
    [string]$DevResourceGroup,
    [string]$AzureTenantId,
    [string]$EnvironmentName = "development",
    [string]$AzureLocation,
    [string]$ContainerAppName,
    [string]$ContainerRegistryName,
    [string]$ContainerAppEnvironmentName,
    [string]$MigrationJobName,
    [string]$ApplicationClientId,
    [SecureString]$DatabaseUrl,
    [string]$MailProvider,
    [string]$MailFromAddress,
    [string]$MailFromName,
    [string]$MailReplyTo,
    [string]$M365TenantId,
    [string]$M365ClientId,
    [ValidateSet('client_secret', 'managed_identity')]
    [string]$M365AuthMode = 'client_secret',
    [SecureString]$M365ClientSecret,
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RequiredValue {
    param([string]$Value, [string]$Prompt)
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return $Value.Trim()
    }
    $resolved = Read-Host $Prompt
    if ([string]::IsNullOrWhiteSpace($resolved)) {
        throw "$Prompt is required."
    }
    return $resolved.Trim()
}

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string]$Command,
        [Parameter(ValueFromRemainingArguments)] [string[]]$Arguments
    )
    $output = & $Command @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($output | Out-String).Trim())
    }
    return $output
}

function Invoke-NativeSensitive {
    param(
        [Parameter(Mandatory)] [string]$Command,
        [Parameter(Mandatory)] [string]$FailureMessage,
        [Parameter(ValueFromRemainingArguments)] [string[]]$Arguments
    )
    # Never surface native output for commands whose arguments contain a
    # secret: some CLI errors echo the full invocation.
    $null = & $Command @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-AzJson {
    param([Parameter(ValueFromRemainingArguments)] [string[]]$Arguments)
    $raw = Invoke-Native az @Arguments --output json
    if ([string]::IsNullOrWhiteSpace(($raw | Out-String))) {
        return $null
    }
    return ($raw | Out-String) | ConvertFrom-Json
}

function Set-GitHubEnvironmentVariable {
    param([string]$Name, [string]$Value, [string]$Repository, [string]$Environment)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-Warning "Skipping empty GitHub variable $Name."
        return
    }
    Invoke-Native gh variable set $Name --repo $Repository --env $Environment --body $Value | Out-Null
    Write-Host "GitHub variable $Name is configured."
}

function Test-DatabaseSecretReference {
    param([object]$EnvironmentVariable)

    # Azure CLI omits `secretRef` entirely for ordinary value-based variables.
    # Read the dynamic JSON properties defensively because StrictMode turns a
    # direct access to an absent property into a terminating error.
    $nameProperty = $EnvironmentVariable.PSObject.Properties['name']
    $secretRefProperty = $EnvironmentVariable.PSObject.Properties['secretRef']
    return (
        $null -ne $nameProperty -and
        $nameProperty.Value -eq 'DATABASE_URL' -and
        $null -ne $secretRefProperty -and
        -not [string]::IsNullOrWhiteSpace([string]$secretRefProperty.Value)
    )
}

function Get-ContainerEnvironmentValue {
    param([object]$ContainerApp, [string]$Name)
    $item = @(
        $ContainerApp.properties.template.containers[0].env |
            Where-Object {
                $_.name -eq $Name -and
                $null -ne $_.PSObject.Properties['value']
            }
    ) | Select-Object -First 1
    if ($null -ne $item) {
        return [string]$item.value
    }
    return ''
}

function Test-ContainerSecretReference {
    param([object]$ContainerApp, [string]$Name)
    return @(
        $ContainerApp.properties.template.containers[0].env |
            Where-Object {
                $_.name -eq $Name -and
                $null -ne $_.PSObject.Properties['secretRef'] -and
                -not [string]::IsNullOrWhiteSpace([string]$_.secretRef)
            }
    ).Count -gt 0
}

if ($ValidateOnly) {
    Write-Host "bootstrap.ps1 parsed successfully; no Azure or GitHub changes were made."
    exit 0
}

Assert-Command az
Assert-Command gh

$GitHubOwner = Resolve-RequiredValue $GitHubOwner "GitHub owner"
$GitHubRepository = Resolve-RequiredValue $GitHubRepository "GitHub repository"
$AzureSubscriptionId = Resolve-RequiredValue $AzureSubscriptionId "Azure subscription ID"
$DevResourceGroup = Resolve-RequiredValue $DevResourceGroup "DEV resource group"
$AzureTenantId = Resolve-RequiredValue $AzureTenantId "Azure tenant ID"
$repository = "$GitHubOwner/$GitHubRepository"

Invoke-Native gh auth status | Out-Null

$account = $null
try {
    $account = Get-AzJson account show
} catch {
    Write-Host "Azure CLI login is required for tenant $AzureTenantId."
    Invoke-Native az login --tenant $AzureTenantId | Out-Null
}
Invoke-Native az account set --subscription $AzureSubscriptionId | Out-Null
$account = Get-AzJson account show
if ($account.tenantId -ne $AzureTenantId) {
    throw "Azure CLI tenant '$($account.tenantId)' does not match '$AzureTenantId'."
}

$resourceGroup = Get-AzJson group show --name $DevResourceGroup
$resourceGroupId = $resourceGroup.id
if ([string]::IsNullOrWhiteSpace($AzureLocation)) {
    $AzureLocation = $resourceGroup.location
}

$encodedEnvironment = [uri]::EscapeDataString($EnvironmentName)
Invoke-Native gh api --method PUT "repos/$repository/environments/$encodedEnvironment" | Out-Null
Write-Host "GitHub environment '$EnvironmentName' exists."

$existingVariables = @{}
$variableRows = Invoke-Native gh variable list --repo $repository --env $EnvironmentName --json "name,value"
foreach ($row in (($variableRows | Out-String) | ConvertFrom-Json)) {
    $existingVariables[$row.name] = $row.value
}

if ([string]::IsNullOrWhiteSpace($ContainerAppName)) {
    $apps = Get-AzJson containerapp list --resource-group $DevResourceGroup
    if ($apps.Count -eq 1) {
        $ContainerAppName = $apps[0].name
    } elseif ($existingVariables.ContainsKey("AZURE_CONTAINER_APP")) {
        $ContainerAppName = $existingVariables["AZURE_CONTAINER_APP"]
    }
}
if ([string]::IsNullOrWhiteSpace($ContainerRegistryName)) {
    $registries = Get-AzJson acr list --resource-group $DevResourceGroup
    if ($registries.Count -eq 1) {
        $ContainerRegistryName = $registries[0].name
    } elseif ($existingVariables.ContainsKey("AZURE_CONTAINER_REGISTRY")) {
        $ContainerRegistryName = $existingVariables["AZURE_CONTAINER_REGISTRY"]
    }
}
if ([string]::IsNullOrWhiteSpace($ContainerAppEnvironmentName)) {
    $environments = Get-AzJson containerapp env list --resource-group $DevResourceGroup
    if ($environments.Count -eq 1) {
        $ContainerAppEnvironmentName = $environments[0].name
    }
}
if ([string]::IsNullOrWhiteSpace($MigrationJobName)) {
    $jobs = Get-AzJson containerapp job list --resource-group $DevResourceGroup
    $migrationJobs = @($jobs | Where-Object { $_.name -match "migrat" })
    if ($migrationJobs.Count -eq 1) {
        $MigrationJobName = $migrationJobs[0].name
    } elseif ($existingVariables.ContainsKey("AZURE_MIGRATION_JOB")) {
        $MigrationJobName = $existingVariables["AZURE_MIGRATION_JOB"]
    }
}

$ContainerAppName = Resolve-RequiredValue $ContainerAppName "Azure Container App name"
$ContainerRegistryName = Resolve-RequiredValue $ContainerRegistryName "Azure Container Registry name"
$MigrationJobName = Resolve-RequiredValue $MigrationJobName "Azure Container Apps migration job name"

$containerApp = Get-AzJson containerapp show --name $ContainerAppName --resource-group $DevResourceGroup
$appBaseUrl = "https://$($containerApp.properties.configuration.ingress.fqdn)"
if ([string]::IsNullOrWhiteSpace($ContainerAppEnvironmentName)) {
    $ContainerAppEnvironmentName = Split-Path -Leaf $containerApp.properties.managedEnvironmentId
}

$job = $null
try {
    $job = Get-AzJson containerapp job show --name $MigrationJobName --resource-group $DevResourceGroup
} catch {
    $provisionCommand = @"
az deployment group create --resource-group $DevResourceGroup --template-file infra/main.bicep --parameters postgresqlAdministratorPassword='<STRONG-PASSWORD>' existingContainerAppName='$ContainerAppName' existingContainerAppEnvironmentName='$ContainerAppEnvironmentName' existingContainerRegistryName='$ContainerRegistryName'
"@.Trim()
    throw @"
Azure Container Apps migration job '$MigrationJobName' was not found in Resource Group '$DevResourceGroup'.

The bootstrap script intentionally does not create infrastructure resources: provision PostgreSQL and the migration job first with the Deploy to Azure template or Bicep. This avoids guessing an image, registry credentials, or a DATABASE_URL for an existing deployment.

From the repository root, provision the missing infrastructure while preserving the existing Container App, Container Apps environment, and registry:
$provisionCommand

After the deployment succeeds, rerun bootstrap.ps1. Do not enter a PostgreSQL URL until the template has completed.
"@
}
$appDatabaseReference = @(
    $containerApp.properties.template.containers[0].env | Where-Object { Test-DatabaseSecretReference $_ }
)
$jobDatabaseReference = @(
    $job.properties.template.containers[0].env | Where-Object { Test-DatabaseSecretReference $_ }
)
if ($appDatabaseReference.Count -eq 0 -or $jobDatabaseReference.Count -eq 0) {
    if ($null -eq $DatabaseUrl) {
        $DatabaseUrl = Read-Host "PostgreSQL DATABASE_URL (input hidden)" -AsSecureString
    }
    $databaseUrlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($DatabaseUrl)
    try {
        $plainDatabaseUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($databaseUrlPointer)
        if ([string]::IsNullOrWhiteSpace($plainDatabaseUrl) -or -not $plainDatabaseUrl.StartsWith("postgresql+psycopg://")) {
            throw "DATABASE_URL must use postgresql+psycopg://."
        }
        if ($appDatabaseReference.Count -eq 0) {
            Invoke-NativeSensitive az "Could not update the Container App database secret. Azure CLI output was redacted." containerapp secret set --name $ContainerAppName --resource-group $DevResourceGroup --secrets "database-url=$plainDatabaseUrl"
            Invoke-Native az containerapp update --name $ContainerAppName --resource-group $DevResourceGroup --set-env-vars "DATABASE_URL=secretref:database-url" | Out-Null
            Write-Host "Configured the existing Container App database secret without replacing other settings."
        }
        if ($jobDatabaseReference.Count -eq 0) {
            Invoke-NativeSensitive az "Could not update the migration job database secret. Azure CLI output was redacted." containerapp job secret set --name $MigrationJobName --resource-group $DevResourceGroup --secrets "database-url=$plainDatabaseUrl"
            Invoke-Native az containerapp job update --name $MigrationJobName --resource-group $DevResourceGroup --set-env-vars "DATABASE_URL=secretref:database-url" | Out-Null
            Write-Host "Configured the migration job database secret."
        }
    } finally {
        if ($databaseUrlPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($databaseUrlPointer)
        }
        $plainDatabaseUrl = $null
    }
} else {
    Write-Host "Existing DATABASE_URL secret references were preserved."
}

if ([string]::IsNullOrWhiteSpace($MailProvider)) {
    $MailProvider = Get-ContainerEnvironmentValue $containerApp 'MAIL_PROVIDER'
    if ([string]::IsNullOrWhiteSpace($MailProvider)) {
        $MailProvider = 'console'
    }
}
$MailProvider = $MailProvider.Trim().ToLowerInvariant()
if ($MailProvider -notin @('console', 'graph')) {
    throw "MAIL_PROVIDER must be 'console' or 'graph'."
}
if ([string]::IsNullOrWhiteSpace($MailFromAddress)) {
    $MailFromAddress = Get-ContainerEnvironmentValue $containerApp 'MAIL_FROM_ADDRESS'
}
if ([string]::IsNullOrWhiteSpace($MailFromName)) {
    $MailFromName = Get-ContainerEnvironmentValue $containerApp 'MAIL_FROM_NAME'
    if ([string]::IsNullOrWhiteSpace($MailFromName)) {
        $MailFromName = 'Volunteer Manager'
    }
}
if ([string]::IsNullOrWhiteSpace($MailReplyTo)) {
    $MailReplyTo = Get-ContainerEnvironmentValue $containerApp 'MAIL_REPLY_TO'
}
if ([string]::IsNullOrWhiteSpace($M365TenantId)) {
    $M365TenantId = Get-ContainerEnvironmentValue $containerApp 'M365_TENANT_ID'
}
if ([string]::IsNullOrWhiteSpace($M365ClientId)) {
    $M365ClientId = Get-ContainerEnvironmentValue $containerApp 'M365_CLIENT_ID'
}

if ($MailProvider -eq 'graph') {
    $MailFromAddress = Resolve-RequiredValue $MailFromAddress 'Microsoft 365 shared mailbox address'
    $M365TenantId = Resolve-RequiredValue $M365TenantId 'Microsoft 365 tenant ID'
    $M365ClientId = Resolve-RequiredValue $M365ClientId 'Microsoft 365 mail application client ID'
    if ($M365AuthMode -eq 'managed_identity') {
        Write-Warning 'Managed Identity mail authentication is reserved for a future release; Graph test delivery will fail safely.'
    } elseif (-not (Test-ContainerSecretReference $containerApp 'M365_CLIENT_SECRET')) {
        if ($null -eq $M365ClientSecret) {
            $M365ClientSecret = Read-Host 'Microsoft 365 application client secret (input hidden)' -AsSecureString
        }
        $clientSecretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($M365ClientSecret)
        try {
            $plainClientSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($clientSecretPointer)
            if ([string]::IsNullOrWhiteSpace($plainClientSecret)) {
                throw 'M365 client secret must not be empty.'
            }
            Invoke-NativeSensitive az "Could not update the Microsoft 365 client secret. Azure CLI output was redacted." containerapp secret set --name $ContainerAppName --resource-group $DevResourceGroup --secrets "m365-client-secret=$plainClientSecret"
            Invoke-Native az containerapp update --name $ContainerAppName --resource-group $DevResourceGroup --set-env-vars 'M365_CLIENT_SECRET=secretref:m365-client-secret' | Out-Null
            Write-Host 'Configured the Microsoft 365 credential as a Container App secret reference.'
        } finally {
            if ($clientSecretPointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($clientSecretPointer)
            }
            $plainClientSecret = $null
        }
    } else {
        Write-Host 'Existing M365_CLIENT_SECRET secret reference was preserved.'
    }
}

Invoke-Native az containerapp update --name $ContainerAppName --resource-group $DevResourceGroup --set-env-vars "MAIL_PROVIDER=$MailProvider" "MAIL_FROM_ADDRESS=$MailFromAddress" "MAIL_FROM_NAME=$MailFromName" "MAIL_REPLY_TO=$MailReplyTo" "M365_TENANT_ID=$M365TenantId" "M365_CLIENT_ID=$M365ClientId" "M365_AUTH_MODE=$M365AuthMode" | Out-Null
Write-Host "Transactional mail configuration applied (provider: $MailProvider; no message was sent)."

if ([string]::IsNullOrWhiteSpace($ApplicationClientId) -and $existingVariables.ContainsKey("AZURE_CLIENT_ID")) {
    $ApplicationClientId = $existingVariables["AZURE_CLIENT_ID"]
}

$secretNames = @(
    (Invoke-Native gh secret list --repo $repository --env $EnvironmentName --json name | Out-String | ConvertFrom-Json).name
)
if ([string]::IsNullOrWhiteSpace($ApplicationClientId) -and $secretNames -contains "AZURE_CLIENT_ID") {
    Write-Warning "AZURE_CLIENT_ID exists as a GitHub secret and cannot be read back. It will be preserved."
    $ApplicationClientId = Resolve-RequiredValue $ApplicationClientId "Existing Azure application (client) ID"
}

$federatedSubject = "repo:${repository}:environment:${EnvironmentName}"
$application = $null
$servicePrincipal = $null
if (-not [string]::IsNullOrWhiteSpace($ApplicationClientId)) {
    try {
        $application = Get-AzJson ad app show --id $ApplicationClientId
        Write-Host "Reusing Entra application $ApplicationClientId."
    } catch {
        throw "The supplied AZURE_CLIENT_ID was not found in tenant $AzureTenantId."
    }
} else {
    $displayName = "$GitHubRepository-$EnvironmentName-github-oidc"
    try {
        $application = Get-AzJson ad app create --display-name $displayName
        $servicePrincipal = Get-AzJson ad sp create --id $application.appId
        $ApplicationClientId = $application.appId
        Write-Host "Created Entra application $ApplicationClientId without a client secret."
    } catch {
        throw "Could not create the Entra application. Ask a tenant administrator to create an app registration and pass -ApplicationClientId. $($_.Exception.Message)"
    }
}

if ($null -eq $servicePrincipal) {
    $servicePrincipal = Get-AzJson ad sp show --id $ApplicationClientId
}

$credentials = @(Get-AzJson ad app federated-credential list --id $application.id)
$matchingCredential = @(
    $credentials | Where-Object {
        $_.issuer -eq "https://token.actions.githubusercontent.com" -and
        $_.subject -eq $federatedSubject -and
        $_.audiences -contains "api://AzureADTokenExchange"
    }
)
if ($matchingCredential.Count -eq 0) {
    $credentialFile = [System.IO.Path]::GetTempFileName()
    try {
        @{
            name = "github-$EnvironmentName"
            issuer = "https://token.actions.githubusercontent.com"
            subject = $federatedSubject
            audiences = @("api://AzureADTokenExchange")
            description = "GitHub Actions OIDC for $repository ($EnvironmentName)"
        } | ConvertTo-Json | Set-Content -LiteralPath $credentialFile -Encoding utf8
        Invoke-Native az ad app federated-credential create --id $application.id --parameters $credentialFile | Out-Null
        Write-Host "Created federated credential for $federatedSubject."
    } finally {
        Remove-Item -LiteralPath $credentialFile -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "Existing matching federated credential was preserved."
}

$roleAssignments = @(Get-AzJson role assignment list --assignee $servicePrincipal.id --scope $resourceGroupId)
$hasContributor = @($roleAssignments | Where-Object { $_.roleDefinitionName -eq "Contributor" }).Count -gt 0
$manualRbacRequired = $false
if (-not $hasContributor) {
    try {
        Invoke-Native az role assignment create --assignee-object-id $servicePrincipal.id --assignee-principal-type ServicePrincipal --role Contributor --scope $resourceGroupId | Out-Null
        Write-Host "Granted Resource Group scoped Contributor."
    } catch {
        $manualRbacRequired = $true
        Write-Warning "Contributor could not be assigned; the current identity likely lacks Microsoft.Authorization/roleAssignments/write."
        Write-Host "Run with a permitted identity:"
        Write-Host "az role assignment create --assignee-object-id $($servicePrincipal.id) --assignee-principal-type ServicePrincipal --role Contributor --scope $resourceGroupId"
    }
} else {
    Write-Host "Existing Resource Group scoped Contributor assignment was preserved."
}

$variables = [ordered]@{
    AZURE_CLIENT_ID = $ApplicationClientId
    AZURE_TENANT_ID = $AzureTenantId
    AZURE_SUBSCRIPTION_ID = $AzureSubscriptionId
    AZURE_RESOURCE_GROUP = $DevResourceGroup
    AZURE_CONTAINER_APP = $ContainerAppName
    AZURE_CONTAINER_REGISTRY = $ContainerRegistryName
    AZURE_CONTAINER_APP_ENVIRONMENT = $ContainerAppEnvironmentName
    AZURE_MIGRATION_JOB = $MigrationJobName
    AZURE_LOCATION = $AzureLocation
    APP_BASE_URL = $appBaseUrl
}
foreach ($entry in $variables.GetEnumerator()) {
    Set-GitHubEnvironmentVariable $entry.Key $entry.Value $repository $EnvironmentName
}

Write-Host "Bootstrap completed for $repository / $EnvironmentName."
Write-Host "OIDC subject: $federatedSubject"
Write-Host "No Azure client secret was created or stored."
if ($manualRbacRequired) {
    Write-Warning "Bootstrap completed with the manual RBAC step shown above still required."
    exit 2
}
