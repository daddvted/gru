.PHONY: gru clean minion

# Registry used for publishing images
REGISTRY?=${REGISTRY_PREFIX}gru
VERSION_FILE=templates/index.html

# Default tag and architecture. Can be overridden
TAG?=$(shell git describe --tags --dirty --always)
ifeq ($(TAG),)
	TAG=latest
endif

ifeq ($(findstring dirty,$(TAG)), dirty)
	TAG=latest
endif


clean:
	rm -f dist/*

## Create a docker image on disk for a specific arch and tag
gru:
	@cp $(VERSION_FILE) "$(VERSION_FILE).bak"
	@sed -i "s/VERSION/$(TAG)/" $(VERSION_FILE)
	docker build --no-cache -f Dockerfile -t $(REGISTRY):$(TAG) .
	@mv "$(VERSION_FILE).bak" $(VERSION_FILE)

push: gru 
	docker push $(REGISTRY):$(TAG)

minion: $(shell find . -type f  -name '*.go')
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -o dist/minion \
	  -ldflags '-s -w -X github.com/ski2per/gru/minion.Version=$(TAG) -extldflags "-static"'

minion-darwin: $(shell find . -type f  -name '*.go')
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=0 go build -o dist/minion \
	  -ldflags '-s -w -X github.com/ski2per/gru/minion.Version=$(TAG) -extldflags "-static"'
