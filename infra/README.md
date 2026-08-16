# Azure infrastructure

`main.bicep` is the source template. `azuredeploy.json` is its checked-in,
CI-verified compilation because the Azure portal Deploy button accepts ARM JSON,
not remote Bicep files.

## Modules

- `monitoring.bicep`: Log Analytics
- `container-app-environment.bicep`: create or reuse an ACA environment
- `container-registry.bicep`: create or reuse ACR
- `postgres.bicep`: PostgreSQL Flexible Server/database/firewall
- `container-app.bicep`: bootstrap web app for new installations
- `migration-job.bicep`: manual Alembic job using the application image
- `storage.bicep`: optional future file/export storage, not database storage

Passing `existingContainerAppEnvironmentName`,
`existingContainerRegistryName`, `existingContainerAppName` or
`existingPostgresServerName` opts into reuse. An existing Container App is only
referenced; Bicep deliberately does not replace its complete configuration.
`scripts/bootstrap.ps1` adds a missing named database secret/reference safely.
The same bootstrap can add provider-neutral mail settings and a missing
`m365-client-secret` reference without printing the credential.

## CLI deployment

Generate a one-time strong password in process memory, deploy to the selected
Resource Group, then remove it from the environment:

```bash
export POSTGRES_ADMIN_PASSWORD="A!9$(openssl rand -base64 24)"
az deployment group create \
  --resource-group <RESOURCE-GROUP> \
  --template-file infra/main.bicep \
  --parameters infra/parameters/dev.bicepparam
unset POSTGRES_ADMIN_PASSWORD
```

To reuse existing Azure resources, add explicit parameters rather than
guessing or recreating them:

```bash
az deployment group create \
  --resource-group <RESOURCE-GROUP> \
  --template-file infra/main.bicep \
  --parameters infra/parameters/dev.bicepparam \
  existingContainerAppEnvironmentName=<ACA-ENVIRONMENT> \
  existingContainerRegistryName=<ACR-NAME> \
  existingContainerAppName=<CONTAINER-APP-NAME>
```

Review `az deployment group what-if` before applying to an existing Resource
Group. The template does not delete Azure Files, existing OIDC or GitHub state.

`demoMode` defaults to `false`; the checked-in DEV parameter file sets it to
`true`. The DEV deployment workflow also enforces the corresponding
`DEMO_MODE` Container App environment value (and accepts an explicit GitHub
Environment variable set to `false`) so existing environments receive the
same visible warning without an infrastructure redeployment.

`appName` defaults to `Volunteer Manager`. The DEV workflow also accepts an
`APP_NAME` GitHub Environment variable and applies it together with the demo
presentation settings, so forks can brand page titles without rebuilding code.

Mail parameters default to a non-sending open-source installation:

```text
mailProvider=console
mailFromAddress=''
mailFromName='Volunteer Manager'
mailReplyTo=''
m365TenantId=''
m365ClientId=''
m365AuthMode=client_secret
m365ClientSecret=''  # secure parameter; Graph mode only
```

For an existing app, prefer `scripts/bootstrap.ps1 -MailProvider graph ...` and
enter `M365ClientSecret` only through its hidden secure-string prompt. Bicep and
bootstrap configure Azure runtime values only; Microsoft Graph/Exchange Online
tenant authorization remains a separate administrator task documented in the
root README.

## Deployment outputs

The deployment returns only non-sensitive names, hostnames and the app URL. It
does not return the PostgreSQL password or complete `DATABASE_URL`. Feed the
resource names to `scripts/bootstrap.ps1`; the script populates GitHub Environment
Variables and preserves matching OIDC state.

## Networking boundary

The DEV default allows Azure-hosted resources through the PostgreSQL special
firewall rule and requires TLS. It does not add `0.0.0.0-255.255.255.255` or a
wildcard client range. Private access should be introduced only with a planned
VNet-compatible Container Apps environment; this template never recreates an
existing environment to force that change.
