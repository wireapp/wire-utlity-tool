package controllers

import (
	"context"
	"time"

	"github.com/go-logr/logr"
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/manager"
)

type ServiceSecretWatcher struct {
	client.Client
	Log logr.Logger
}

func (s *ServiceSecretWatcher) SetupWithManager(mgr manager.Manager) error {
	// Start watching services and secrets on controller start
	// go s.listServicesAndSecrets()
	go func() {
		if mgr.GetCache().WaitForCacheSync(context.Background()) {
			s.Log.Info("Cache is synced, starting to list services and secrets")
			s.listServicesAndSecrets()
		}
		ticker := time.NewTicker(30 * time.Second) // Adjust the interval as needed
		defer ticker.Stop()
		for range ticker.C {
			s.listServicesAndSecrets()
		}
	}()
	return nil
}

func (s *ServiceSecretWatcher) listServicesAndSecrets() {
	ctx := context.Background()

	// List services
	var services corev1.ServiceList
	if err := s.Client.List(ctx, &services); err != nil {
		s.Log.Error(err, "failed to list services")
		return
	}
	for _, svc := range services.Items {
		s.Log.Info("Discovered Service", "name", svc.Name, "namespace", svc.Namespace, "clusterIP", svc.Spec.ClusterIP)
	}

	// List secrets (wire-server secrets, for example)
	var secrets corev1.SecretList
	if err := s.Client.List(ctx, &secrets); err != nil {
		s.Log.Error(err, "failed to list secrets")
		return
	}
	for _, sec := range secrets.Items {
		s.Log.Info("Found Secret", "name", sec.Name, "namespace", sec.Namespace, "type", sec.Type)
	}
}
