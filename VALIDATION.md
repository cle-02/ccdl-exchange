# Validation results

- Terraform 1.13.5: terraform fmt -check passed.
- Python: all 3 unittest cases passed (stub cannot acknowledge orders; stop on FIFO failure; successful processing acknowledgement).
- terraform init -backend=false succeeded with signed hashicorp/aws 6.71.0 and hashicorp/archive 2.8.1.
- terraform providers lock -platform=linux_amd64 independently verified the signed registry checksums.
- terraform validate could NOT complete: the local extracted AWS provider package was reported as not matching the lock-file checksums, including after one clean reinstallation. Checksums were not bypassed or manually altered. Run terraform init and terraform validate in your WSL environment before planning; if the mismatch recurs, investigate the provider download/cache rather than disabling verification.
- No AWS plan, deployment, integration test, performance test, matching engine, or ledger behavior was validated.

Only source, tests, documentation, and the provider lock file are packaged. No provider binaries, credentials, Terraform state, or plans are included.
