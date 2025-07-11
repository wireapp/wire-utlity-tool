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
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/manager"
)

type ServiceSecretWatcher struct {
	client.Client
	Log                   logr.Logger
	lastDiscoveredSecrets map[string]corev1.Secret
	deploymentName        string
	deploymentNamespace   string
}

func (s *ServiceSecretWatcher) SetupWithManager(mgr manager.Manager) error {
	// Initialize tracking
	s.lastDiscoveredSecrets = make(map[string]corev1.Secret)
	s.deploymentName = "wire-utility-operator" // Your deployment name
	s.deploymentNamespace = "default"          // Your deployment namespace

	go func() {
		if mgr.GetCache().WaitForCacheSync(context.Background()) {
			s.Log.Info("Cache is synced, starting to discover and update deployment")
			s.discoverAndUpdateDeployment()
		}
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			s.discoverAndUpdateDeployment()
		}
	}()
	return nil
}

func (s *ServiceSecretWatcher) discoverAndUpdateDeployment() {
	_, secrets := s.listServicesAndSecrets()

	// Filter for service-related secrets
	serviceSecrets := s.filterServiceSecrets(secrets)

	if len(serviceSecrets) > 0 && s.hasNewSecrets(serviceSecrets) {
		s.Log.Info("New service secrets discovered, updating deployment",
			"newSecrets", len(serviceSecrets))

		if err := s.updateDeploymentWithSecrets(serviceSecrets); err != nil {
			s.Log.Error(err, "Failed to update deployment with secrets")
		} else {
			s.Log.Info("Successfully updated deployment with service secrets")
			// Update tracking
			s.updateSecretsTracking(serviceSecrets)
		}
	}
}

func (s *ServiceSecretWatcher) listServicesAndSecrets() ([]corev1.Service, []corev1.Secret) {
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

func (s *ServiceSecretWatcher) filterServiceSecrets(secrets []corev1.Secret) []corev1.Secret {
	var serviceSecrets []corev1.Secret

	for _, secret := range secrets {
		if s.isServiceSecret(secret) {
			serviceSecrets = append(serviceSecrets, secret)
			s.Log.Info("Identified as service secret", "name", secret.Name, "type", s.getSecretServiceType(secret))
		}
	}

	return serviceSecrets
}

func (s *ServiceSecretWatcher) isServiceSecret(secret corev1.Secret) bool {
	// Skip system secrets
	if secret.Type == corev1.SecretTypeServiceAccountToken ||
		secret.Type == corev1.SecretTypeDockercfg ||
		secret.Type == corev1.SecretTypeDockerConfigJson {
		return false
	}

	name := strings.ToLower(secret.Name)

	// Check for known service patterns in name
	servicePatterns := []string{"minio", "rabbitmq", "cassandra", "postgres", "redis", "mysql"}
	for _, pattern := range servicePatterns {
		if strings.Contains(name, pattern) {
			return true
		}
	}

	// Check for service-like data keys
	hasServiceKeys := false
	serviceKeys := []string{"username", "password", "access-key", "secret-key", "endpoint", "host", "port"}

	for key := range secret.Data {
		keyLower := strings.ToLower(key)
		for _, serviceKey := range serviceKeys {
			if strings.Contains(keyLower, serviceKey) {
				hasServiceKeys = true
				break
			}
		}
		if hasServiceKeys {
			break
		}
	}

	return hasServiceKeys
}

func (s *ServiceSecretWatcher) getSecretServiceType(secret corev1.Secret) string {
	name := strings.ToLower(secret.Name)

	if strings.Contains(name, "minio") {
		return "minio"
	}
	if strings.Contains(name, "rabbitmq") {
		return "rabbitmq"
	}
	if strings.Contains(name, "cassandra") {
		return "cassandra"
	}
	if strings.Contains(name, "postgres") {
		return "postgres"
	}
	if strings.Contains(name, "redis") {
		return "redis"
	}

	return "unknown"
}

func (s *ServiceSecretWatcher) hasNewSecrets(currentSecrets []corev1.Secret) bool {
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

func (s *ServiceSecretWatcher) updateDeploymentWithSecrets(secrets []corev1.Secret) error {
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

		// Create volume mount
		volumeMount := corev1.VolumeMount{
			Name:      fmt.Sprintf("secret-%s", secret.Name),
			MountPath: mountPath,
			ReadOnly:  true,
		}
		newVolumeMounts = append(newVolumeMounts, volumeMount)

		// Create volume
		volume := corev1.Volume{
			Name: fmt.Sprintf("secret-%s", secret.Name),
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: secret.Name,
				},
			},
		}
		newVolumes = append(newVolumes, volume)

		s.Log.Info("Prepared secret mount",
			"secret", secret.Name,
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

func (s *ServiceSecretWatcher) removeServiceSecretMounts(mounts []corev1.VolumeMount) []corev1.VolumeMount {
	var filtered []corev1.VolumeMount
	for _, mount := range mounts {
		// Keep mounts that are not service secret mounts
		if !strings.HasPrefix(mount.MountPath, "/etc/wire-services/") {
			filtered = append(filtered, mount)
		}
	}
	return filtered
}

func (s *ServiceSecretWatcher) removeServiceSecretVolumes(volumes []corev1.Volume) []corev1.Volume {
	var filtered []corev1.Volume
	for _, volume := range volumes {
		// Keep volumes that don't start with "secret-"
		if !strings.HasPrefix(volume.Name, "secret-") {
			filtered = append(filtered, volume)
		}
	}
	return filtered
}

func (s *ServiceSecretWatcher) updateSecretsTracking(secrets []corev1.Secret) {
	s.lastDiscoveredSecrets = make(map[string]corev1.Secret)
	for _, secret := range secrets {
		s.lastDiscoveredSecrets[secret.Name] = secret
	}
}

// calculateSecretsHash creates a hash of the current secrets for comparison
func (s *ServiceSecretWatcher) calculateSecretsHash(secrets []corev1.Secret) string {
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
