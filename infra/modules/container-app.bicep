@description('Azure region for Container Apps.')
param location string

@description('Container App name.')
param containerAppName string

@description('Container Apps environment resource ID.')
param environmentId string

@description('Initial container image. The deployment workflow replaces it with a commit-tagged application image.')
param image string

@description('Registry login server.')
param registryServer string

@description('Registry username.')
param registryUsername string

@secure()
@description('Registry password stored only as a Container App secret.')
param registryPassword string

@secure()
@description('SQLAlchemy PostgreSQL URL stored only as a Container App secret.')
param databaseUrl string

@description('Show the global demo-mode warning in the application UI.')
param demoMode bool = false

@description('Transactional mail provider.')
param mailProvider string = 'console'

@description('Configured sender/shared mailbox address.')
param mailFromAddress string = ''

@description('Transactional mail sender display name.')
param mailFromName string = 'ST. PRIDE Volunteer Manager'

@description('Optional transactional mail Reply-To address.')
param mailReplyTo string = ''

@description('Microsoft 365 tenant ID.')
param m365TenantId string = ''

@description('Entra application client ID for Graph mail.')
param m365ClientId string = ''

@description('Graph credential mode.')
param m365AuthMode string = 'client_secret'

@secure()
@description('Entra client secret for Graph mail.')
param m365ClientSecret string = ''

@description('Resource tags applied to the Container App.')
param tags object = {}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
      }
      registries: [
        {
          server: registryServer
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: concat([
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registryPassword
        }
      ], empty(m365ClientSecret) ? [] : [
        {
          name: 'm365-client-secret'
          value: m365ClientSecret
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'volunteer-manager'
          image: image
          env: concat([
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'ENVIRONMENT'
              value: 'development'
            }
            {
              name: 'AUTH_MODE'
              value: 'easyauth'
            }
            {
              name: 'SEED_DEMO_DATA'
              value: 'false'
            }
            {
              name: 'DEMO_MODE'
              value: string(demoMode)
            }
            {
              name: 'MAIL_PROVIDER'
              value: mailProvider
            }
            {
              name: 'MAIL_FROM_ADDRESS'
              value: mailFromAddress
            }
            {
              name: 'MAIL_FROM_NAME'
              value: mailFromName
            }
            {
              name: 'MAIL_REPLY_TO'
              value: mailReplyTo
            }
            {
              name: 'M365_TENANT_ID'
              value: m365TenantId
            }
            {
              name: 'M365_CLIENT_ID'
              value: m365ClientId
            }
            {
              name: 'M365_AUTH_MODE'
              value: m365AuthMode
            }
          ], empty(m365ClientSecret) ? [] : [
            {
              name: 'M365_CLIENT_SECRET'
              secretRef: 'm365-client-secret'
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

output id string = app.id
output name string = app.name
output fqdn string = app.properties.configuration.ingress.fqdn
