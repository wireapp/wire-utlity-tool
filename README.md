# wire-utility-operator
// TODO(user): Add simple overview of use/purpose

## Description
// TODO(user): An in-depth paragraph about your project and overview of use

## Getting Started

### Prerequisites
- go version v1.24.0+
- docker version 17.03+.
- kubectl version v1.11.3+.
- Access to a Kubernetes v1.11.3+ cluster.

### To Deploy on the cluster
**Build and push the image to the location specified by `IMG`:**

```sh
make docker-build docker-push IMG=<some-registry>/wire-utility-operator:tag
```


**build and push the docker image:**

```sh
make docker-build-push
```

### By providing a Helm Chart

The manifest flies to deploy the controller and related rabc components will be transported to a helm chart 


## Contributing
// TODO(user): Add detailed information on how you would like others to contribute to this project
