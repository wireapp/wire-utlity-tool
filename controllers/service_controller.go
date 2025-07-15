package controllers

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/go-logr/logr"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/manager"
)

const (
	serviceTypeMinio     = "minio"
	serviceTypeRabbitMQ  = "rabbitmq"
	serviceTypeCassandra = "cassandra"
	serviceTypePostgres  = "postgres"
	serviceTypeRedis     = "redis"
)

var (
	TARGET_SERVICE_TYPES = []string{
		serviceTypeMinio,
		serviceTypeRabbitMQ,
		serviceTypeCassandra,
		serviceTypePostgres,
		serviceTypeRedis,
	}
)

// Add this helper function after the constants section
func sanitizeVolumeName(name string) string {
	// Replace dots and other invalid characters with dashes
	sanitized := strings.ReplaceAll(name, ".", "-")
	sanitized = strings.ReplaceAll(sanitized, "_", "-")
	// Ensure it doesn't end with a dash
	sanitized = strings.Trim(sanitized, "-")
	return sanitized
}

type ServiceDiscoveryController struct {
	client.Client
	Log                   logr.Logger
	lastDiscoveredSecrets map[string]corev1.Secret
	lastDiscoveredHash    string
	deploymentName        string
	deploymentNamespace   string
	configMapName         string
}

func (s *ServiceDiscoveryController) SetupWithManager(mgr manager.Manager) error {
	// Initialize tracking
	s.lastDiscoveredSecrets = make(map[string]corev1.Secret)
	s.deploymentName = "wire-utility-operator" // deployment name
	s.deploymentNamespace = "default"          // deployment namespace
	s.configMapName = "service-endpoints"      // ConfigMap to store discovered service endpoints

	// Set up the controller to watch for changes using proper controller-runtime pattern
	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Secret{}).                                          // Watch for Secret changes
		Watches(&corev1.Service{}, &handler.EnqueueRequestForObject{}). // Watch for Service changes
		Complete(s)
}

// Reconcile is the main reconciliation logic triggered by Kubernetes events
func (s *ServiceDiscoveryController) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	s.Log.Info("Reconciliation triggered", "request", req)

	//  List all services and secrets
	services, secrets := s.listServicesAndSecrets()

	// Process service discovery
	serviceEndpoints := s.discoverServiceEndpoints(services)
	if len(serviceEndpoints) > 0 {
		if err := s.createOrUpdateServiceConfigMap(serviceEndpoints); err != nil {
			s.Log.Error(err, "Failed to create/update service endpoints ConfigMap")
			return ctrl.Result{RequeueAfter: time.Minute}, err
		} else {
			s.Log.Info("Successfully updated service endpoints ConfigMap", "endpoints", len(serviceEndpoints))
		}
	}

	//  Process secrets
	serviceSecrets := s.filterServiceSecrets(secrets)

	if len(serviceSecrets) > 0 && s.hasNewSecrets(serviceSecrets) {
		s.Log.Info("New service secrets discovered, updating deployment",
			"newSecrets", len(serviceSecrets))

		if err := s.updateDeploymentWithSecrets(serviceSecrets); err != nil {
			s.Log.Error(err, "Failed to update deployment with secrets")
			return ctrl.Result{RequeueAfter: time.Minute}, err
		} else {
			s.Log.Info("Successfully updated deployment with service secrets")
			// Update tracking
			s.updateSecretsTracking(serviceSecrets)
		}
	}

	s.Log.Info("Reconciliation completed successfully")
	return ctrl.Result{RequeueAfter: time.Minute * 5}, nil // Requeue every 5 minutes as backup
}

func (s *ServiceDiscoveryController) listServicesAndSecrets() ([]corev1.Service, []corev1.Secret) {
	ctx := context.Background()

	// List services
	var services corev1.ServiceList
	if err := s.Client.List(ctx, &services); err != nil {
		s.Log.Error(err, "failed to list services")
		return nil, nil
	}

	var serviceList []corev1.Service
	for _, svc := range services.Items {
		s.Log.Info("Discovered Service", "name", svc.Name, "namespace", svc.Namespace, "clusterIP", svc.Spec.ClusterIP)
		serviceList = append(serviceList, svc)
	}

	// List secrets
	var secrets corev1.SecretList
	if err := s.Client.List(ctx, &secrets); err != nil {
		s.Log.Error(err, "failed to list secrets")
		return serviceList, nil
	}

	var secretList []corev1.Secret
	for _, sec := range secrets.Items {
		s.Log.Info("Found Secret", "name", sec.Name, "namespace", sec.Namespace, "type", sec.Type)
		secretList = append(secretList, sec)
	}

	return serviceList, secretList
}

func (s *ServiceDiscoveryController) filterServiceSecrets(secrets []corev1.Secret) []corev1.Secret {
	var serviceSecrets []corev1.Secret

	for _, secret := range secrets {
		if s.isServiceSecret(secret) {
			serviceSecrets = append(serviceSecrets, secret)
			s.Log.Info("Identified as service secret", "name", secret.Name, "type", s.getSecretServiceType(secret))
		}
	}

	return serviceSecrets
}

func (s *ServiceDiscoveryController) isServiceSecret(secret corev1.Secret) bool {
	// Skip system secrets
	if secret.Type == corev1.SecretTypeServiceAccountToken ||
		secret.Type == corev1.SecretTypeDockercfg ||
		secret.Type == corev1.SecretTypeDockerConfigJson {
		return false
	}

	name := strings.ToLower(secret.Name)

	if strings.Contains(name, "helm.release") || strings.HasPrefix(name, "sh.helm") {
		return false
	}

	for _, serviceType := range TARGET_SERVICE_TYPES {
		if strings.Contains(name, serviceType) {
			return true
		}
	}

	return false
}

func (s *ServiceDiscoveryController) getSecretServiceType(secret corev1.Secret) string {
	secretName := strings.ToLower(secret.Name)

	// Map secret names to service types
	for _, serviceType := range TARGET_SERVICE_TYPES {
		if strings.Contains(secretName, serviceType) {
			return serviceType
		}
	}
	return "unknown"
}

func (s *ServiceDiscoveryController) hasNewSecrets(currentSecrets []corev1.Secret) bool {
	// Check deployment annotation to see if we've already processed these secrets
	ctx := context.Background()
	deployment := &appsv1.Deployment{}
	err := s.Client.Get(ctx, client.ObjectKey{
		Name:      s.deploymentName,
		Namespace: s.deploymentNamespace,
	}, deployment)
	if err != nil {
		s.Log.Info("Could not get deployment, assuming new secrets", "error", err.Error())
		return true
	}

	// Check if the deployment already has the same secrets processed
	if deployment.Spec.Template.Annotations != nil {
		lastSecretsCount := deployment.Spec.Template.Annotations["wire-utility/secrets-count"]
		lastSecretsHash := deployment.Spec.Template.Annotations["wire-utility/secrets-hash"]

		currentSecretsHash := s.calculateSecretsHash(currentSecrets)
		currentSecretsCount := fmt.Sprintf("%d", len(currentSecrets))

		if lastSecretsCount == currentSecretsCount && lastSecretsHash == currentSecretsHash {
			s.Log.Info("Deployment already has same secrets processed, skipping update",
				"secretsCount", currentSecretsCount,
				"secretsHash", currentSecretsHash)

			// Initialize tracking if empty
			if len(s.lastDiscoveredSecrets) == 0 {
				s.updateSecretsTracking(currentSecrets)
			}
			return false
		}

		s.Log.Info("Secrets have changed",
			"oldCount", lastSecretsCount, "newCount", currentSecretsCount,
			"oldHash", lastSecretsHash, "newHash", currentSecretsHash)
	}

	// Fallback to original logic for additional checks
	if len(s.lastDiscoveredSecrets) > 0 {
		// Check if number of secrets changed
		if len(currentSecrets) != len(s.lastDiscoveredSecrets) {
			s.Log.Info("Number of secrets changed", "old", len(s.lastDiscoveredSecrets), "new", len(currentSecrets))
			return true
		}

		// Check if any secret is new or modified
		for _, secret := range currentSecrets {
			lastSecret, exists := s.lastDiscoveredSecrets[secret.Name]
			if !exists {
				s.Log.Info("New secret detected", "name", secret.Name)
				return true
			}

			// Check if secret was modified (simple comparison)
			if secret.ResourceVersion != lastSecret.ResourceVersion {
				s.Log.Info("Secret modified", "name", secret.Name)
				return true
			}
		}
	}

	return true
}

func (s *ServiceDiscoveryController) updateDeploymentWithSecrets(secrets []corev1.Secret) error {
	ctx := context.Background()

	// Get current deployment
	deployment := &appsv1.Deployment{}
	err := s.Client.Get(ctx, client.ObjectKey{
		Name:      s.deploymentName,
		Namespace: s.deploymentNamespace,
	}, deployment)
	if err != nil {
		return fmt.Errorf("failed to get deployment %s/%s: %w", s.deploymentNamespace, s.deploymentName, err)
	}

	s.Log.Info("Found deployment to update",
		"name", deployment.Name,
		"currentContainers", len(deployment.Spec.Template.Spec.Containers),
		"currentVolumes", len(deployment.Spec.Template.Spec.Volumes))

	// Prepare volume mounts and volumes
	var newVolumeMounts []corev1.VolumeMount
	var newVolumes []corev1.Volume

	for _, secret := range secrets {
		serviceType := s.getSecretServiceType(secret)
		mountPath := fmt.Sprintf("/etc/wire-services/%s/%s", serviceType, secret.Name)

		sanitizedVolumeName := fmt.Sprintf("secret-%s", sanitizeVolumeName(secret.Name))
		// Create volume mount
		volumeMount := corev1.VolumeMount{
			Name:      sanitizedVolumeName,
			MountPath: mountPath,
			ReadOnly:  true,
		}
		newVolumeMounts = append(newVolumeMounts, volumeMount)

		// Create volume
		volume := corev1.Volume{
			Name: sanitizedVolumeName,
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: secret.Name,
				},
			},
		}
		newVolumes = append(newVolumes, volume)

		s.Log.Info("Prepared secret mount",
			"secret", secret.Name,
			"volumeName", sanitizedVolumeName,
			"type", serviceType,
			"mountPath", mountPath)
	}

	// Update the first container (assuming single container deployment)
	if len(deployment.Spec.Template.Spec.Containers) > 0 {
		container := &deployment.Spec.Template.Spec.Containers[0]

		// Remove old service secret mounts
		container.VolumeMounts = s.removeServiceSecretMounts(container.VolumeMounts)

		// Add new mounts
		container.VolumeMounts = append(container.VolumeMounts, newVolumeMounts...)

		s.Log.Info("Updated container volume mounts",
			"container", container.Name,
			"totalMounts", len(container.VolumeMounts))
	}

	// Update volumes
	deployment.Spec.Template.Spec.Volumes = s.removeServiceSecretVolumes(deployment.Spec.Template.Spec.Volumes)
	deployment.Spec.Template.Spec.Volumes = append(deployment.Spec.Template.Spec.Volumes, newVolumes...)

	// Add annotation to trigger rolling update and track state
	if deployment.Spec.Template.Annotations == nil {
		deployment.Spec.Template.Annotations = make(map[string]string)
	}
	deployment.Spec.Template.Annotations["wire-utility/secrets-update"] = time.Now().Format(time.RFC3339)
	deployment.Spec.Template.Annotations["wire-utility/secrets-count"] = fmt.Sprintf("%d", len(secrets))
	deployment.Spec.Template.Annotations["wire-utility/secrets-hash"] = s.calculateSecretsHash(secrets)

	// Update the deployment
	if err := s.Client.Update(ctx, deployment); err != nil {
		return fmt.Errorf("failed to update deployment: %w", err)
	}

	s.Log.Info("Successfully updated deployment",
		"deployment", s.deploymentName,
		"secretsCount", len(secrets),
		"volumesCount", len(newVolumes),
		"volumeMountsCount", len(newVolumeMounts))

	return nil
}

func (s *ServiceDiscoveryController) removeServiceSecretMounts(mounts []corev1.VolumeMount) []corev1.VolumeMount {
	var filtered []corev1.VolumeMount
	for _, mount := range mounts {
		// Keep mounts that are not service secret mounts
		if !strings.HasPrefix(mount.MountPath, "/etc/wire-services/") {
			filtered = append(filtered, mount)
		}
	}
	return filtered
}

func (s *ServiceDiscoveryController) removeServiceSecretVolumes(volumes []corev1.Volume) []corev1.Volume {
	var filtered []corev1.Volume
	for _, volume := range volumes {
		// Keep volumes that don't start with "secret-"
		if !strings.HasPrefix(volume.Name, "secret-") {
			filtered = append(filtered, volume)
		}
	}
	return filtered
}

func (s *ServiceDiscoveryController) updateSecretsTracking(secrets []corev1.Secret) {
	s.lastDiscoveredSecrets = make(map[string]corev1.Secret)
	for _, secret := range secrets {
		s.lastDiscoveredSecrets[secret.Name] = secret
	}
}

// calculateSecretsHash creates a hash of the current secrets for comparison
func (s *ServiceDiscoveryController) calculateSecretsHash(secrets []corev1.Secret) string {
	var secretNames []string
	for _, secret := range secrets {
		// Use name and resource version for hash
		secretNames = append(secretNames, fmt.Sprintf("%s:%s", secret.Name, secret.ResourceVersion))
	}

	// Sort for consistent hash
	sort.Strings(secretNames)

	// Create a simple hash by joining the strings
	hashString := strings.Join(secretNames, "|")

	// Use a simple hash function (you could use crypto/md5 for more robust hashing)
	hash := 0
	for _, char := range hashString {
		hash = hash*31 + int(char)
	}

	return fmt.Sprintf("%x", hash)
}

// Service discovery and ConfigMap management methods

// ServiceEndpoint represents a discovered service endpoint
type ServiceEndpoint struct {
	Name      string
	Type      string
	Host      string
	Port      int32
	Namespace string
}

func (s *ServiceDiscoveryController) discoverServiceEndpoints(services []corev1.Service) []ServiceEndpoint {
	var endpoints []ServiceEndpoint

	for _, service := range services {
		serviceName := strings.ToLower(service.Name)

		// Check if this service matches our patterns
		serviceType := ""
		for _, targetType := range TARGET_SERVICE_TYPES {
			if strings.HasPrefix(serviceName, targetType) {
				serviceType = targetType
				break
			}
		}

		if serviceType == "" {
			continue // Skip services that don't match our patterns
		}

		// Extract primary port (usually the first port)
		if len(service.Spec.Ports) == 0 {
			s.Log.Info("Service has no ports, skipping", "service", service.Name)
			continue
		}

		port := service.Spec.Ports[0].Port

		// Always use DNS name for better reliability
		host := service.Name
		if service.Namespace != s.deploymentNamespace {
			host = fmt.Sprintf("%s.%s.svc.cluster.local", service.Name, service.Namespace)
		}

		endpoint := ServiceEndpoint{
			Name:      service.Name,
			Type:      serviceType,
			Host:      host,
			Port:      port,
			Namespace: service.Namespace,
		}

		endpoints = append(endpoints, endpoint)
		s.Log.Info("Discovered service endpoint",
			"name", endpoint.Name,
			"type", endpoint.Type,
			"host", endpoint.Host,
			"port", endpoint.Port)
	}

	return endpoints
}

// Update the createOrUpdateServiceConfigMap method to use cleaner key names
func (s *ServiceDiscoveryController) createOrUpdateServiceConfigMap(endpoints []ServiceEndpoint) error {
	ctx := context.Background()

	// Build ConfigMap data with cleaner key names
	data := make(map[string]string)

	for _, endpoint := range endpoints {
		// Use service type for cleaner keys (minio, rabbitmq, etc.)
		serviceType := endpoint.Type

		// Create simple, predictable key names
		data[fmt.Sprintf("%s-host", serviceType)] = endpoint.Host
		data[fmt.Sprintf("%s-port", serviceType)] = fmt.Sprintf("%d", endpoint.Port)
		data[fmt.Sprintf("%s-endpoint", serviceType)] = fmt.Sprintf("http://%s:%d", endpoint.Host, endpoint.Port)

		s.Log.Info("Added service endpoint to ConfigMap",
			"serviceType", serviceType,
			"endpoint", data[fmt.Sprintf("%s-endpoint", serviceType)])
	}

	// Create ConfigMap object
	configMap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      s.configMapName,
			Namespace: s.deploymentNamespace,
			Labels: map[string]string{
				"app":        "wire-utility-operator",
				"component":  "service-discovery",
				"managed-by": "wire-utility-operator",
			},
		},
		Data: data,
	}

	// Try to get existing ConfigMap
	existing := &corev1.ConfigMap{}
	err := s.Client.Get(ctx, client.ObjectKey{
		Name:      s.configMapName,
		Namespace: s.deploymentNamespace,
	}, existing)

	if err != nil {
		// ConfigMap doesn't exist, create it
		s.Log.Info("Creating new service endpoints ConfigMap", "name", s.configMapName, "entries", len(data))
		if err := s.Client.Create(ctx, configMap); err != nil {
			return fmt.Errorf("failed to create ConfigMap: %w", err)
		}
	} else {
		// ConfigMap exists, update it
		existing.Data = data
		existing.Labels = configMap.Labels
		s.Log.Info("Updating existing service endpoints ConfigMap", "name", s.configMapName, "entries", len(data))
		if err := s.Client.Update(ctx, existing); err != nil {
			return fmt.Errorf("failed to update ConfigMap: %w", err)
		}
	}

	// Ensure the deployment mounts this ConfigMap
	return s.ensureConfigMapMounted()
}

// ensureConfigMapMounted ensures the operator deployment mounts the service endpoints ConfigMap
func (s *ServiceDiscoveryController) ensureConfigMapMounted() error {
	ctx := context.Background()

	// Get current deployment
	deployment := &appsv1.Deployment{}
	err := s.Client.Get(ctx, client.ObjectKey{
		Name:      s.deploymentName,
		Namespace: s.deploymentNamespace,
	}, deployment)
	if err != nil {
		return fmt.Errorf("failed to get deployment %s/%s: %w", s.deploymentNamespace, s.deploymentName, err)
	}

	// Check if ConfigMap is already mounted
	configMapVolumeName := fmt.Sprintf("configmap-%s", s.configMapName)
	configMapMountPath := "/etc/wire-services/endpoints"

	needsUpdate := false

	// Check volumes
	hasConfigMapVolume := false
	for _, volume := range deployment.Spec.Template.Spec.Volumes {
		if volume.Name == configMapVolumeName {
			hasConfigMapVolume = true
			break
		}
	}

	if !hasConfigMapVolume {
		// Add ConfigMap volume
		volume := corev1.Volume{
			Name: configMapVolumeName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{
						Name: s.configMapName,
					},
				},
			},
		}
		deployment.Spec.Template.Spec.Volumes = append(deployment.Spec.Template.Spec.Volumes, volume)
		needsUpdate = true
		s.Log.Info("Added ConfigMap volume to deployment")
	}

	// Check volume mounts in the first container
	if len(deployment.Spec.Template.Spec.Containers) > 0 {
		container := &deployment.Spec.Template.Spec.Containers[0]

		hasConfigMapMount := false
		for _, mount := range container.VolumeMounts {
			if mount.Name == configMapVolumeName {
				hasConfigMapMount = true
				break
			}
		}

		if !hasConfigMapMount {
			// Add ConfigMap volume mount
			volumeMount := corev1.VolumeMount{
				Name:      configMapVolumeName,
				MountPath: configMapMountPath,
				ReadOnly:  true,
			}
			container.VolumeMounts = append(container.VolumeMounts, volumeMount)
			needsUpdate = true
			s.Log.Info("Added ConfigMap volume mount to container", "mountPath", configMapMountPath)
		}
	}

	// Update deployment if needed
	if needsUpdate {
		// Add annotation to trigger rolling update
		if deployment.Spec.Template.Annotations == nil {
			deployment.Spec.Template.Annotations = make(map[string]string)
		}
		deployment.Spec.Template.Annotations["wire-utility/config-update"] = time.Now().Format(time.RFC3339)

		if err := s.Client.Update(ctx, deployment); err != nil {
			return fmt.Errorf("failed to update deployment with ConfigMap mount: %w", err)
		}

		s.Log.Info("Successfully updated deployment with ConfigMap mount")
	}
	return nil
}
