@description('Azure region for Container Apps.')
param location string

@description('Migration job name.')
param jobName string

@description('Container Apps environment resource ID.')
param environmentId string

@description('Application image used for Alembic migrations.')
param image string

@description('Registry login server.')
param registryServer string

@description('Registry username.')
param registryUsername string

@secure()
@description('Registry password stored only as a job secret.')
param registryPassword string

@secure()
@description('SQLAlchemy PostgreSQL URL stored only as a job secret.')
param databaseUrl string

@description('Resource tags applied to the migration job.')
param tags object = {}

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
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
          name: 'alembic'
          image: image
          command: [
            '/bin/sh'
          ]
          args: [
            '-c'
            'alembic upgrade head && alembic current'
          ]
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output id string = job.id
output name string = job.name
