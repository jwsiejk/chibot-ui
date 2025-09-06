# Environment Matrix
| Env     | Vendors       | Keys Present | Notes |
|---------|---------------|--------------|-------|
| local   | mocked        | no           | CI uses sqlite, no external calls |
| staging | real          | yes          | Nightly voice loop smoke runs |
| prod    | real          | yes          | SLOs monitored; alerts configured |
