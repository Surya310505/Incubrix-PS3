# Development failures retained for transparency

- Initial system `pytest` invocation failed because pytest was not installed;
  dependencies were installed into a temporary environment outside the
  repository.
- Initial `pytest` console-script invocation could not import the local `app`
  package. Running `python -m pytest` fixed the import path.
- The first local test import attempted to create `/app/data` and failed on the
  macOS host. The default data path now resolves relative to the project, and
  tests use isolated writable storage.
- Initial caption burn-in used the FFmpeg `subtitles` filter, unavailable in
  the installed host FFmpeg build. Caption overlays now use Pillow-generated
  transparent images, avoiding that optional filter.
- An initial API idempotency test did not start FastAPI's lifespan and failed
  because the jobs table had not been initialized. Tests now use
  `TestClient`'s lifespan context.
- The first preserved smoke run passed a relative output directory, causing
  relative FFmpeg concat-list paths to be resolved twice. Concat entries now
  use resolved absolute paths; the failed attempt is retained in the test pack.
- Invoking the batch runner with the system interpreter failed because that
  interpreter has no installed `httpx`; invoking it inside the temporary project
  virtual environment works. The documented setup installs the dependency in
  the activated environment.
- The Docker Compose test service initially called the `pytest` console script,
  which did not add `/app` to the import path and could not import the `app`
  package. Switching the service command to `python -m pytest` fixed collection.
- The first frame-quality container run correctly rejected a static mock clip
  after it was looped into a frozen output. The footage-render fixture now uses
  moving test-pattern frames; separate black/frozen fixtures verify rejection.
- Docker image build and the full Compose test profile now run successfully.
  The default API port 8000 was occupied during the local container smoke test,
  so the API was verified on host port 18000 instead. Hosted deployment remains
  unverified.

These were development failures, not fabricated media-provider or human
evaluation results. No known weak visual relevance score is presented as a
measured result; relevance evaluation has not been performed.
