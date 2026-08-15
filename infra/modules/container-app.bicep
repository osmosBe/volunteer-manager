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
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registryPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'volunteer-manager'
          image: image
          env: [
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
          ]
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
