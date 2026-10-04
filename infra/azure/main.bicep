targetScope = 'resourceGroup'

@description('Nombre corto del entorno. Usar lab o prod; cada uno despliega en su propio resource group.')
@allowed([
  'lab'
  'prod'
])
param environmentName string = 'lab'

@description('Región seleccionada para los recursos regionales del borrador.')
param location string = 'eastus2'

@description('Región del Azure SQL de DIFE. La oferta gratuita de Azure SQL solo se pudo crear en australiaeast (eastus2 y eastus no admiten servidores nuevos; centralus, westus2, southcentralus y canadacentral fallan con la oferta gratuita).')
param sqlLocation string = 'australiaeast'

@description('Cuenta SQL inicial. Cambiala por un usuario administrador dedicado al entorno.')
param sqlAdministratorLogin string

@secure()
@description('Contraseña temporal del administrador SQL. Ingresarla por un canal seguro al desplegar, nunca en el archivo de parámetros.')
param sqlAdministratorPassword string

@description('Object ID del principal que realiza el despliegue; recibe permisos de secretos limitados a este Key Vault.')
param deploymentPrincipalObjectId string

@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deploymentPrincipalType string = 'User'

@description('Imagen pública de la API en GHCR. El primer despliegue usa una imagen de ejemplo; el CD la reemplaza por la etiqueta del commit.')
param apiImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Activar solo después de cargar en Key Vault los secretos de runtime (ver README). Si faltan, la revisión no arranca.')
param enableAppSecrets bool = false

@description('Base MongoDB nueva y dedicada a QA en este entorno.')
param mongoDatabaseName string = 'qalabspbvi_qa_${environmentName}'

@description('Remitente verificado en Brevo para MFA y notificaciones.')
param brevoSenderEmail string = ''

@description('URL de la cola SQS de avisos QA (infra/aws/notifications.yaml). Vacía: los avisos salen directo por Brevo. Requiere los secretos aws-access-key-id y aws-secret-access-key en Key Vault.')
param notificationsQueueUrl string = ''

@description('Activa el login MFA automatizable para qa-evidencia. Requiere los secretos qa-automation-token y qa-automation-emails en Key Vault.')
param enableQaAutomation bool = false

@description('URL del gateway ISO 20022 en Render. Vacía: los mensajes se generan en proceso. Requiere el secreto iso-gateway-token en Key Vault.')
param isoGatewayUrl string = ''

var resourceToken = '${uniqueString(subscription().id, resourceGroup().id, location, environmentName)}1'
var logAnalyticsName = 'azla${resourceToken}'
var identityName = 'azid${resourceToken}'
var keyVaultName = toLower('azkv${resourceToken}')
var managedEnvironmentName = 'azcae${resourceToken}'
var containerAppName = 'azca${resourceToken}'
// Servidor dedicado a DIFE en la región donde se pudo crear la oferta gratuita.
var sqlServerName = toLower('azsqlqalab${take(sqlLocation, 6)}1')
var sqlDatabaseName = 'qalabdife'
var keyVaultSecretsOfficerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)
// Secretos de runtime: nombre en Key Vault -> variable de entorno de la API.
// Los valores se cargan por CLI (az keyvault secret set), nunca en este archivo ni en Git.
var appSecretMap = concat(
  [
    { name: 'database-url', env: 'DATABASE_URL' }
    { name: 'dife-database-url', env: 'DIFE_DATABASE_URL' }
    { name: 'dice-database-url', env: 'DICE_DATABASE_URL' }
    { name: 'mongodb-url', env: 'MONGODB_URL' }
    { name: 'auth-secret-key', env: 'AUTH_SECRET_KEY' }
    { name: 'brevo-api-key', env: 'BREVO_API_KEY' }
  ],
  // Usuario IAM de AWS que solo puede enviar a la cola de avisos.
  empty(notificationsQueueUrl) ? [] : [
    { name: 'aws-access-key-id', env: 'AWS_ACCESS_KEY_ID' }
    { name: 'aws-secret-access-key', env: 'AWS_SECRET_ACCESS_KEY' }
  ],
  empty(isoGatewayUrl) ? [] : [
    { name: 'iso-gateway-token', env: 'ISO_GATEWAY_TOKEN' }
  ],
  // Cuenta de pruebas E2E cuyo código MFA queda en un registro protegido por token.
  enableQaAutomation ? [
    { name: 'qa-automation-token', env: 'QA_AUTOMATION_TOKEN' }
    { name: 'qa-automation-emails', env: 'QA_AUTOMATION_EMAILS' }
  ] : []
)
var appSecrets = [
  for s in appSecretMap: {
    name: s.name
    keyVaultUrl: 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/${s.name}'
    identity: identity.id
  }
]
var appSecretEnv = [
  for s in appSecretMap: {
    name: s.env
    secretRef: s.name
  }
]
var appPlainEnv = [
  { name: 'APP_ENV', value: environmentName }
  { name: 'MONGODB_DATABASE', value: mongoDatabaseName }
  { name: 'BREVO_SENDER_EMAIL', value: brevoSenderEmail }
  { name: 'BREVO_SENDER_NAME', value: 'QALabSPBVI' }
  // El ejecutor QA llama a la API por su propio dominio: Front Door reenvía con ese Host,
  // y la sesión del usuario solo se reenvía cuando el host destino coincide con el entrante.
  { name: 'QA_TARGET_BASE_URL', value: 'https://${containerAppName}.${managedEnvironment.properties.defaultDomain}' }
  // Multinube: avisos QA por AWS SQS + Lambda y mensajes ISO 20022 por el gateway en Render.
  { name: 'NOTIFICATIONS_QUEUE_URL', value: notificationsQueueUrl }
  { name: 'AWS_REGION', value: 'us-east-1' }
  // Sin pool de conexiones: las bases (Neon, Azure SQL gratuito, OCI) solo se activan con
  // solicitudes reales y pueden pausarse; nada mantiene sesiones abiertas.
  { name: 'DATABASE_POOL', value: 'null' }
  { name: 'ISO_GATEWAY_URL', value: isoGatewayUrl }
]

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

resource keyVaultSecretsOfficerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, keyVaultSecretsOfficerRoleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleDefinitionId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deploymentSecretsOfficerRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, deploymentPrincipalObjectId, keyVaultSecretsOfficerRoleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleDefinitionId
    principalId: deploymentPrincipalObjectId
    principalType: deploymentPrincipalType
  }
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: sqlLocation
  properties: {
    administratorLogin: sqlAdministratorLogin
    administratorLoginPassword: sqlAdministratorPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: sqlDatabaseName
  location: sqlLocation
  // Oferta gratuita de Azure SQL: 100.000 vCore-segundos y 32 GB al mes; si se agota, la
  // base se pausa hasta el mes siguiente en lugar de cobrar.
  sku: {
    name: 'GP_S_Gen5_2'
    tier: 'GeneralPurpose'
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    useFreeLimit: true
    freeLimitExhaustionBehavior: 'AutoPause'
    autoPauseDelay: 60
    minCapacity: json('0.5')
    zoneRedundant: false
    requestedBackupStorageRedundancy: 'Local'
  }
}

resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'azure-services'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource sqlAdminPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azpwd${resourceToken}'
  properties: {
    value: sqlAdministratorPassword
    contentType: 'Credential for the dedicated DIFE Azure SQL server'
  }
  dependsOn: [
    keyVaultSecretsOfficerRoleAssignment
    deploymentSecretsOfficerRoleAssignment
  ]
}

resource sqlAdminLoginSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'azusr${resourceToken}'
  properties: {
    value: sqlAdministratorLogin
    contentType: 'Credential for the dedicated DIFE Azure SQL server'
  }
  dependsOn: [
    keyVaultSecretsOfficerRoleAssignment
    deploymentSecretsOfficerRoleAssignment
  ]
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: managedEnvironmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: enableAppSecrets ? appSecrets : []
      // La imagen está en GHCR y es pública: no hace falta registro ni credenciales.
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: enableAppSecrets ? concat(appPlainEnv, appSecretEnv) : appPlainEnv
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
  dependsOn: [
    keyVaultSecretsOfficerRoleAssignment
  ]
}

output environmentName string = environmentName
output location string = location
output containerAppName string = containerApp.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output sqlServerFullyQualifiedDomainName string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDatabase.name
output keyVaultName string = keyVault.name
