targetScope = 'resourceGroup'

@description('Nombre corto del entorno. Usar lab o prod; cada uno despliega en su propio resource group.')
@allowed([
  'lab'
  'prod'
])
param environmentName string = 'lab'

@description('Región seleccionada para los recursos regionales del borrador.')
param location string = 'centralus'

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

@description('Imagen de la API. El primer despliegue usa una imagen de ejemplo; el CD la reemplaza por la etiqueta del commit.')
param apiImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Activar solo después de cargar en Key Vault los secretos de runtime (ver README). Si faltan, la revisión no arranca.')
param enableAppSecrets bool = false

@description('Base MongoDB nueva y dedicada a QA en este entorno.')
param mongoDatabaseName string = 'qalabspbvi_qa_${environmentName}'

@description('Remitente verificado en Brevo para MFA y notificaciones.')
param brevoSenderEmail string = ''

var resourceToken = '${uniqueString(subscription().id, resourceGroup().id, location, environmentName)}1'
var logAnalyticsName = 'azla${resourceToken}'
var identityName = 'azid${resourceToken}'
var registryName = toLower('azacr${resourceToken}')
var keyVaultName = toLower('azkv${resourceToken}')
var managedEnvironmentName = 'azcae${resourceToken}'
var containerAppName = 'azca${resourceToken}'
var sqlServerName = toLower('azsql${resourceToken}')
var sqlDatabaseName = 'azdb${resourceToken}'
var staticWebAppName = 'azswa${resourceToken}'
var frontDoorProfileName = 'azafd${resourceToken}'
var frontDoorEndpointName = 'azfde${resourceToken}'
var apiOriginGroupName = 'azoga${resourceToken}'
var webOriginGroupName = 'azogw${resourceToken}'
var apiOriginName = 'azora${resourceToken}'
var webOriginName = 'azorw${resourceToken}'
var apiRouteName = 'azrta${resourceToken}'
var webRouteName = 'azrtw${resourceToken}'
var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsOfficerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)
// Secretos de runtime: nombre en Key Vault -> variable de entorno de la API.
// Los valores se cargan por CLI (az keyvault secret set), nunca en este archivo ni en Git.
var appSecretMap = [
  { name: 'database-url', env: 'DATABASE_URL' }
  { name: 'dife-database-url', env: 'DIFE_DATABASE_URL' }
  { name: 'dice-database-url', env: 'DICE_DATABASE_URL' }
  { name: 'mongodb-url', env: 'MONGODB_URL' }
  { name: 'auth-secret-key', env: 'AUTH_SECRET_KEY' }
  { name: 'brevo-api-key', env: 'BREVO_API_KEY' }
]
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
  // Supuesto a validar en laboratorio: el ejecutor QA llama a la API por la entrada pública /api.
  { name: 'QA_TARGET_BASE_URL', value: 'https://${frontDoorEndpoint.properties.hostName}/api' }
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

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    roleDefinitionId: acrPullRoleDefinitionId
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
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
  location: location
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
  location: location
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 2147483648
    zoneRedundant: false
    readScale: 'Disabled'
    requestedBackupStorageRedundancy: 'Local'
  }
}

resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'azfw${resourceToken}'
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

resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    stagingEnvironmentPolicy: 'Enabled'
  }
}

resource frontDoorProfile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: frontDoorProfileName
  location: 'global'
  sku: {
    name: 'Standard_AzureFrontDoor'
  }
  properties: {}
}

resource frontDoorEndpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: frontDoorProfile
  name: frontDoorEndpointName
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
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
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        corsPolicy: {
          allowedOrigins: [
            'https://${frontDoorEndpoint.properties.hostName}'
          ]
          allowedMethods: [
            'GET'
            'POST'
            'PUT'
            'PATCH'
            'DELETE'
            'OPTIONS'
          ]
          allowedHeaders: [
            '*'
          ]
          exposeHeaders: []
          maxAge: 600
          allowCredentials: true
        }
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
    acrPullRoleAssignment
    keyVaultSecretsOfficerRoleAssignment
  ]
}

resource apiOriginGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: frontDoorProfile
  name: apiOriginGroupName
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/health'
      probeRequestType: 'GET'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 100
    }
    sessionAffinityState: 'Disabled'
  }
}

resource webOriginGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: frontDoorProfile
  name: webOriginGroupName
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/'
      probeRequestType: 'GET'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 100
    }
    sessionAffinityState: 'Disabled'
  }
}

resource apiOrigin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: apiOriginGroup
  name: apiOriginName
  properties: {
    enabledState: 'Enabled'
    hostName: containerApp.properties.configuration.ingress.fqdn
    httpPort: 80
    httpsPort: 443
    originHostHeader: containerApp.properties.configuration.ingress.fqdn
    priority: 1
    weight: 1000
    enforceCertificateNameCheck: true
  }
}

resource webOrigin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: webOriginGroup
  name: webOriginName
  properties: {
    enabledState: 'Enabled'
    hostName: staticWebApp.properties.defaultHostname
    httpPort: 80
    httpsPort: 443
    originHostHeader: staticWebApp.properties.defaultHostname
    priority: 1
    weight: 1000
    enforceCertificateNameCheck: true
  }
}

// FastAPI expone /health, /auth, /qa... sin prefijo: igual que el proxy de Vite en local,
// Front Door retira /api antes de reenviar (/api/health -> /health).
resource apiRuleSet 'Microsoft.Cdn/profiles/ruleSets@2024-02-01' = {
  parent: frontDoorProfile
  name: 'azrsapi${environmentName}'
}

resource stripApiPrefixRule 'Microsoft.Cdn/profiles/ruleSets/rules@2024-02-01' = {
  parent: apiRuleSet
  name: 'stripapiprefix'
  properties: {
    order: 1
    conditions: [
      {
        name: 'UrlPath'
        parameters: {
          typeName: 'DeliveryRuleUrlPathMatchConditionParameters'
          operator: 'BeginsWith'
          matchValues: [
            'api/'
          ]
          negateCondition: false
          transforms: []
        }
      }
    ]
    actions: [
      {
        name: 'UrlRewrite'
        parameters: {
          typeName: 'DeliveryRuleUrlRewriteActionParameters'
          sourcePattern: '/api/'
          destination: '/'
          preserveUnmatchedPath: true
        }
      }
    ]
    matchProcessingBehavior: 'Stop'
  }
}

resource apiRoute 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: frontDoorEndpoint
  name: apiRouteName
  dependsOn: [
    stripApiPrefixRule
  ]
  properties: {
    originGroup: {
      id: apiOriginGroup.id
    }
    ruleSets: [
      {
        id: apiRuleSet.id
      }
    ]
    originPath: '/'
    supportedProtocols: [
      'Http'
      'Https'
    ]
    patternsToMatch: [
      '/api/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
  }
}

resource webRoute 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: frontDoorEndpoint
  name: webRouteName
  properties: {
    originGroup: {
      id: webOriginGroup.id
    }
    supportedProtocols: [
      'Http'
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
  }
}

output environmentName string = environmentName
output location string = location
output containerRegistryLoginServer string = registry.properties.loginServer
output containerAppName string = containerApp.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output staticWebAppName string = staticWebApp.name
output staticWebAppHostname string = staticWebApp.properties.defaultHostname
output frontDoorHostname string = frontDoorEndpoint.properties.hostName
output sqlServerFullyQualifiedDomainName string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDatabase.name
output keyVaultName string = keyVault.name
