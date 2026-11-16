# Evaluation suites

`tpcds-v1.json` holds 20 questions, with reference SQL, for the full TPC-DS warehouse
(the 24 tables that [`../cloudera-tpcds`](../cloudera-tpcds/README.md) builds in Impala) and the repository's published TPC-DS model
(`models/published/tpcds.ossie.yaml`). It covers store, catalog and web sales and returns.

```sh
helios eval examples/eval/tpcds-v1.json --model tpcds
```

The sample project's ten questions, answerable on the store-sales star alone, are what
`helios init --sample` writes as `sample-suite.json`. The suite format and `helios eval`
are described in [helios-serve's README](../../packages/helios-serve/README.md).
