# ThinkRoom security policy

This is a local single-user application. Keep development listeners on loopback;
do not expose development Compose or reuse its example credentials in production.
Only ingest documents you are permitted to use and send to the configured model
provider. Model, embedding and OCR services can receive source content. Uploaded
documents and their instructions are untrusted input; model output needs human review.

Use GitHub private vulnerability reporting when enabled. If unavailable, request
a private contact channel with a minimal issue; omit personal data, credentials
and exploit details. This file does not establish that private reporting is enabled.

Revoke/rotate exposed credentials first. Deleting a file does not remove old
commits, releases, logs or caches. Do not attach raw diagnostic bundles. Use synthetic
reproductions for source-scope, upload, file-access and data-leak bugs.
