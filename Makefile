IMG ?= sukisuk/wire-utility-operator:latest

.PHONY: docker-build
docker-build:
	docker build -t ${IMG} .

.PHONY: docker-push
docker-push:
	docker push ${IMG}

.PHONY: docker-build-push
docker-build-push: docker-build docker-push

.PHONY: deploy
deploy:
	kubectl apply -f config/rbac/
	kubectl apply -f config/deployment.yaml