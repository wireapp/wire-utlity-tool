PostgreSQL Endpoint Manager — CI: runtime vs test images (precise)

Purpose
- Explain the two-image strategy used by CI: a minimal runtime image (pushed to registry) and a fuller test image (builder-target) used only for CI tests and local debugging.
- Provide exact commands and recommended debug workflows.

Definitions
- runtime image
  - Built from the Dockerfile final stage (no `--target`).
  - Base: distroless Python (minimal, no shell, no package manager).
  - Purpose: runtime artifact pushed to registry and used in production CronJobs.
  - Not included: `psql`, `apt`, shell utilities (`sh`, `bash`, `ls`), package build tools.

- test image (builder/test image)
  - Built from the Dockerfile builder stage: `--target builder`.
  - Includes build-time and test-only tools: `postgresql-client` (`psql`), `bash`, `curl`, `jq`, compilers/libpq-dev, and the installed site-packages under the `/install` prefix.
  - Purpose: run CI unit/integration tests and for local interactive debugging.

Why this split
- Keeps the runtime image minimal (fast pulls, smaller attack surface, predictable startup) while enabling comprehensive CI tests using a fuller environment.
- Tests run from the test image and mount repository `scripts/` (so test code / fixtures are not baked into the runtime image).

CI workflow (what it does, precisely)
1. Build test image (single platform) from the builder stage:
   - docker/build-push-action builds with `target: builder`, tag `test-image:latest` (not pushed).
2. Run tests inside test image:
   - The workflow mounts repository `scripts/` into `/app/scripts` and invokes the test harness with Python:
     - python3 /app/scripts/test-postgres-endpoint-manager.py --comprehensive
3. If tests pass and this is not a PR, build and push the multi-platform final runtime image (no --target).
   - The final image is what gets tagged/pushed to the registry.

Exact commands (CI-local parity)
- Build the runtime (final) image locally:
```bash
make build-pg-manager
# equivalent: docker build -f Dockerfile.postgres-endpoint-manager -t <registry>/<repo>:<tag> .
```

- Build the test/builder image locally:
```bash
make build-pg-manager-test
# equivalent: docker build -f Dockerfile.postgres-endpoint-manager --target builder -t <image>-test:latest .
```

- Run the comprehensive test harness using the test image (matches CI):
```bash
docker run --rm \
  -v $(pwd)/scripts:/app/scripts:ro \
  --entrypoint python3 \
  <image>-test:latest /app/scripts/test-postgres-endpoint-manager.py --comprehensive
```

- Sanity-check the runtime image locally (this will show Python but not shell or psql):
```bash
# prints Python but 'psql' should be absent
docker run --rm --entrypoint sh <runtime-image>:latest -c "python --version 2>&1 || true; which psql 2>/dev/null || echo 'psql: absent'"
```

Debugging workflows (precise, recommended)
- Interactive shell and inspection (use the test image):
```bash
# start an interactive shell inside the fuller test image
docker run --rm -it --entrypoint bash <image>-test:latest
# inside the container you have: psql, curl, jq, bash
# run the manager script in test mode (script is in /usr/local/bin or mount it):
python /usr/local/bin/postgres-endpoint-manager.py --test
# or run the test harness directly (if scripts are mounted):
python /app/scripts/test-postgres-endpoint-manager.py --scenario healthy_cluster
```

- Inspect final runtime image filesystem (no shell available inside distroless; use docker create + docker cp):
```bash
# create a temporary container from the runtime image
ctr=$(docker create <runtime-image>:latest)
# copy the installed Python artifacts out for inspection
docker cp ${ctr}:/usr/local ./tmp_usr_local
# remove the temporary container
docker rm ${ctr}
# now inspect ./tmp_usr_local on host
ls -la ./tmp_usr_local
```

- Run a one-off Python probe in the runtime image (no shell required):
```bash
# Display sys.path and installed package list from the runtime image
docker run --rm --entrypoint python <runtime-image>:latest -c "import sys, pkgutil; print(sys.executable); print('\n'.join(sys.path)); print([m.name for m in pkgutil.iter_modules()])"
```

Best practices and recommendations (concise)
- Never rely on shell-based debugging in production image; use the `-test` image for development and CI debugging.
- Keep tests and test data out of the runtime image. Mount tests in CI/test runs instead of baking them in.
- If you need to inspect runtime-installed packages, use the docker create + docker cp pattern above.
- For emergency debugging where you need a shell but want runtime binaries, run a debug container based on the test image and install or mount what you need there.

CI maintainer checklist (quick)
- Ensure QUAY credentials/secrets are present (unchanged from previous workflow).
- The workflow builds `test-image:latest` from the builder target and runs tests by mounting `scripts/`.
- The final multi-platform build continues to build/push the runtime image only.

If you want more automation
- I can add a short `docs/ci-debug.md` with common troubleshooting commands and common failure patterns observed in CI.
- I can add a small Makefile target that automates the `docker create` + `docker cp` inspection step.
