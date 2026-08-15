@description('Azure region for the registry.')
param location string

@description('Name used when a new registry is created.')
param registryName string

@description('Existing registry to reuse. Leave empty to create one.')
param existingRegistryName string = ''

@description('Resource tags applied to a new registry.')
param tags object = {}

resource newRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (empty(existingRegistryName)) {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
    policies: {
      retentionPolicy: {
        days: 7
        status: 'disabled'
      }
    }
  }
}

var selectedRegistryName = empty(existingRegistryName) ? registryName : existingRegistryName

output id string = resourceId('Microsoft.ContainerRegistry/registries', selectedRegistryName)
output name string = selectedRegistryName
output loginServer string = '${selectedRegistryName}.azurecr.io'
