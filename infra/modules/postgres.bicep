@description('Azure region for PostgreSQL.')
param location string

@description('Name used when a new PostgreSQL Flexible Server is created.')
param serverName string

@description('Existing PostgreSQL Flexible Server to reuse. Leave empty to create one.')
param existingServerName string = ''

@description('PostgreSQL administrator login.')
param administratorLogin string

@secure()
@description('PostgreSQL administrator password. Never commit this value.')
param administratorPassword string

@description('Application database name.')
param databaseName string = 'volunteer'

@description('Resource tags applied to a new server.')
param tags object = {}

var selectedServerName = empty(existingServerName) ? serverName : existingServerName

resource newServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = if (empty(existingServerName)) {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: 32
    }
  }
}

resource applicationDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  name: '${selectedServerName}/${databaseName}'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [
    newServer
  ]
}

// This rule permits Azure-hosted workloads, not unrestricted public IP ranges.
// Use private networking in environments where the existing ACA environment
// already has compatible VNet integration.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  name: '${selectedServerName}/AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
  dependsOn: [
    newServer
  ]
}

output id string = resourceId('Microsoft.DBforPostgreSQL/flexibleServers', selectedServerName)
output name string = selectedServerName
output fullyQualifiedDomainName string = '${selectedServerName}.postgres.database.azure.com'
output databaseName string = databaseName
