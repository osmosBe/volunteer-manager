@description('Azure region for the Container Apps environment.')
param location string

@description('Name used when a new environment is created.')
param environmentName string

@description('Existing environment to reuse. Leave empty to create one.')
param existingEnvironmentName string = ''

@description('Log Analytics workspace customer ID.')
param logAnalyticsCustomerId string

@secure()
@description('Log Analytics workspace shared key.')
param logAnalyticsSharedKey string

@description('Resource tags applied to a new environment.')
param tags object = {}

resource newEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = if (empty(existingEnvironmentName)) {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource existingEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' existing = if (!empty(existingEnvironmentName)) {
  name: existingEnvironmentName
}

output id string = empty(existingEnvironmentName) ? newEnvironment.id : existingEnvironment.id
output name string = empty(existingEnvironmentName) ? newEnvironment.name : existingEnvironment.name
