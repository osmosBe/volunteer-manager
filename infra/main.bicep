targetScope = 'resourceGroup'

@description('Short lowercase prefix used to generate resource names.')
@minLength(3)
@maxLength(18)
param namePrefix string = 'volunteer'

@description('Deployment environment label.')
@allowed([
  'dev'
  'production'
])
param environmentName string = 'dev'

@description('Azure region. Existing resources must be in a compatible region.')
param location string = resourceGroup().location

@description('Existing Container Apps environment to preserve and reuse. Leave empty to create one.')
param existingContainerAppEnvironmentName string = ''

@description('Existing Azure Container Registry to preserve and reuse. Leave empty to create one.')
param existingContainerRegistryName string = ''

@description('Existing Container App to preserve. Leave empty to create a bootstrap app.')
param existingContainerAppName string = ''

@description('Existing PostgreSQL Flexible Server to reuse. Leave empty to create one.')
param existingPostgresServerName string = ''

@description('PostgreSQL administrator login.')
param postgresqlAdministratorLogin string = 'volunteeradmin'

@secure()
@description('Strong PostgreSQL administrator password. This is stored only in Azure resource secrets.')
param postgresqlAdministratorPassword string

@description('Initial public bootstrap image. GitHub Actions replaces it with the application image.')
param bootstrapImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Create a storage account for future files/exports. It is not used for the database.')
param deployStorage bool = true

@description('Show a global warning that the application is running in demo mode.')
param demoMode bool = false

@description('Transactional mail provider. Console performs no external delivery.')
@allowed([
  'console'
  'graph'
])
param mailProvider string = 'console'

@description('Configured sender/shared mailbox address. Required before sending mail.')
param mailFromAddress string = ''

@description('Display name used for transactional mail.')
param mailFromName string = 'ST. PRIDE Volunteer Manager'

@description('Optional Reply-To address.')
param mailReplyTo string = ''

@description('Microsoft 365 tenant ID. Required only for the Graph provider.')
param m365TenantId string = ''

@description('Entra application client ID. Required only for the Graph provider.')
param m365ClientId string = ''

@description('Graph credential mode. Managed Identity is reserved for future support.')
@allowed([
  'client_secret'
  'managed_identity'
])
param m365AuthMode string = 'client_secret'

@secure()
@description('Entra client secret. Stored only as a Container App secret; leave empty for console mode.')
param m365ClientSecret string = ''

var suffix = uniqueString(subscription().subscriptionId, resourceGroup().id, namePrefix)
var normalizedPrefix = toLower(replace(namePrefix, '-', ''))
var generatedEnvironmentName = '${namePrefix}-${environmentName}-env'
var generatedRegistryName = take('${normalizedPrefix}${environmentName}${suffix}', 50)
var generatedAppName = '${namePrefix}-${environmentName}-app'
var generatedJobName = '${namePrefix}-${environmentName}-migrate'
var generatedPostgresName = take('${namePrefix}-${environmentName}-pg-${suffix}', 63)
var generatedStorageName = take('${normalizedPrefix}${environmentName}${suffix}', 24)
var workspaceName = '${namePrefix}-${environmentName}-logs'
var tags = {
  application: 'st-pride-volunteer-manager'
  environment: environmentName
  managedBy: 'bicep'
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    workspaceName: workspaceName
    tags: tags
  }
}

module containerAppEnvironment 'modules/container-app-environment.bicep' = {
  name: 'container-app-environment'
  params: {
    location: location
    environmentName: generatedEnvironmentName
    existingEnvironmentName: existingContainerAppEnvironmentName
    logAnalyticsCustomerId: monitoring.outputs.customerId
    logAnalyticsSharedKey: listKeys(resourceId('Microsoft.OperationalInsights/workspaces', workspaceName), '2023-09-01').primarySharedKey
    tags: tags
  }
}

module registry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    location: location
    registryName: generatedRegistryName
    existingRegistryName: existingContainerRegistryName
    tags: tags
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgresql'
  params: {
    location: location
    serverName: generatedPostgresName
    existingServerName: existingPostgresServerName
    administratorLogin: postgresqlAdministratorLogin
    administratorPassword: postgresqlAdministratorPassword
    databaseName: 'volunteer'
    tags: tags
  }
}

module storage 'modules/storage.bicep' = if (deployStorage) {
  name: 'storage'
  params: {
    location: location
    storageAccountName: generatedStorageName
    tags: tags
  }
}

var selectedRegistryName = empty(existingContainerRegistryName) ? generatedRegistryName : existingContainerRegistryName
var registryCredentials = listCredentials(resourceId('Microsoft.ContainerRegistry/registries', selectedRegistryName), '2023-07-01')
var databaseUrl = 'postgresql+psycopg://${postgresqlAdministratorLogin}:${uriComponent(postgresqlAdministratorPassword)}@${postgres.outputs.fullyQualifiedDomainName}:5432/${postgres.outputs.databaseName}?sslmode=require'

module containerApp 'modules/container-app.bicep' = if (empty(existingContainerAppName)) {
  name: 'container-app'
  params: {
    location: location
    containerAppName: generatedAppName
    environmentId: containerAppEnvironment.outputs.id
    image: bootstrapImage
    registryServer: registry.outputs.loginServer
    registryUsername: registryCredentials.username
    registryPassword: registryCredentials.passwords[0].value
    databaseUrl: databaseUrl
    demoMode: demoMode
    mailProvider: mailProvider
    mailFromAddress: mailFromAddress
    mailFromName: mailFromName
    mailReplyTo: mailReplyTo
    m365TenantId: m365TenantId
    m365ClientId: m365ClientId
    m365AuthMode: m365AuthMode
    m365ClientSecret: m365ClientSecret
    tags: tags
  }
}

resource existingContainerApp 'Microsoft.App/containerApps@2024-03-01' existing = if (!empty(existingContainerAppName)) {
  name: existingContainerAppName
}

module migrationJob 'modules/migration-job.bicep' = {
  name: 'migration-job'
  params: {
    location: location
    jobName: generatedJobName
    environmentId: containerAppEnvironment.outputs.id
    image: bootstrapImage
    registryServer: registry.outputs.loginServer
    registryUsername: registryCredentials.username
    registryPassword: registryCredentials.passwords[0].value
    databaseUrl: databaseUrl
    tags: tags
  }
}

var selectedContainerAppName = empty(existingContainerAppName) ? generatedAppName : existingContainerAppName
var selectedContainerAppFqdn = empty(existingContainerAppName) ? containerApp!.outputs.fqdn : existingContainerApp!.properties.configuration.ingress.fqdn

output containerAppEnvironmentName string = containerAppEnvironment.outputs.name
output containerRegistryName string = registry.outputs.name
output containerRegistryLoginServer string = registry.outputs.loginServer
output containerAppName string = selectedContainerAppName
output containerAppUrl string = 'https://${selectedContainerAppFqdn}'
output migrationJobName string = migrationJob.outputs.name
output postgresServerName string = postgres.outputs.name
output postgresHost string = postgres.outputs.fullyQualifiedDomainName
output postgresDatabaseName string = postgres.outputs.databaseName
output storageAccountName string = deployStorage ? storage!.outputs.name : ''
