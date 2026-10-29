# Demo app

A 20-line Go web server (`/health` answers `ok`) built into a `FROM scratch` image, used to show Dockyard end to end.

```bash
docker build -t dockyard-demo/api:1.0 examples/demo-app   # needs internet once, to pull the Go builder
cd examples/demo-app && dockyard pack --out ../../dist-bundles
```
