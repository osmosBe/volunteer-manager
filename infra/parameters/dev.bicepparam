using '../main.bicep'

param namePrefix = 'volunteer'
param environmentName = 'dev'
param location = 'northeurope'
param deployStorage = true

// Supply this only at deployment time. Bicep reads the process environment and
// never writes the password into a parameter file or deployment output.
param postgresqlAdministratorPassword = readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')
