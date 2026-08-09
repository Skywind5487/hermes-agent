# Focused e2-micro VM gate — 2026-08-09

Source package SHA-256: `3068b6209e5a714b9e19928ca7c4388c161b0ae39f0a668b74698c951405c031`

Git head: `444c161218b00166bda73f2ec5a21f250e2049bc`

## Decision

- **Production resolver candidate:** `python_dict_memo`.
- **Global successful-row-lookup budget:** `B = 1500`.
- TEMP and Fixed3 remain reference evidence only.

Why:

- Hermes-normal depth0 median: no-memo `0.037460 ms`, memo `0.038469 ms`.
- Hermes-normal depth1 median: no-memo `0.051469 ms`, memo `0.053704 ms`.
- Historical depth14/size15 worst-ranked median: no-memo `0.592984 ms`, memo `0.124840 ms` (~4.75x faster).
- Historical depth14/size15 full-consume median: no-memo `0.619249 ms`, memo `0.114357 ms` (~5.42x faster).
- TEMP / Fixed3 medians are ~3.4x–7.9x slower than the best KISS candidate across the focused cells.
- The frozen-corpus hard candidate envelope previously established for `C <= 1000` is `<=1254` successful node visits. `B=1500` covers that with ~20% headroom while keeping pathological work bounded.

The safety sweep on e2-micro contains non-monotonic 100–200ms wall-time stalls from the already-attributed shared-core quota regime. Do not infer a sharp algorithmic latency cliff from those individual samples. `B=1500` is selected primarily by bracketing legitimate logical work (`1254`) with a modest safety margin, not by fitting the noisy wall-time curve.

## Validity receipt

- Full mode (`quick=false`).
- Production DB opened: `false`.
- 35s CPU precondition actually consumed ~35.14s process CPU over ~35.16s wall.
- 11 focused/contract tests passed.
- Git worktree was clean.
- Runner executable was `/usr/bin/python3`, linked SQLite 3.40.1. This differs from Hermes-managed production runtime versions seen elsewhere; that caveat matters most to planner-heavy references, not to the no-memo-vs-memo point-lookup mechanism decision. Production integration should still be exercised by normal Hermes tests/runtime before merge.

## Raw receipt

```json
{
  "timestamp_utc": "2026-08-09T13:22:40Z",
  "hostname": "hermes",
  "platform": "Linux-6.1.0-50-cloud-amd64-x86_64-with-glibc2.36",
  "uname": [
    "Linux",
    "hermes",
    "6.1.0-50-cloud-amd64",
    "#1 SMP PREEMPT_DYNAMIC Debian 6.1.176-1 (2026-07-02)",
    "x86_64",
    ""
  ],
  "python": {
    "executable": "/usr/bin/python3",
    "version": "3.11.2 (main, May 12 2026, 05:17:27) [GCC 12.2.0]",
    "implementation": "CPython",
    "compiler": "GCC 12.2.0"
  },
  "sqlite": {
    "sqlite_version": "3.40.1",
    "sqlite_source_id": "2022-12-28 14:03:47 df5c253c0b3dd24916e4ec7cf77d3db5294cc9fd45ae7b9c5e82ad8197f3alt1",
    "compile_options": [
      "ATOMIC_INTRINSICS=1",
      "COMPILER=gcc-12.2.0",
      "DEFAULT_AUTOVACUUM",
      "DEFAULT_CACHE_SIZE=-2000",
      "DEFAULT_FILE_FORMAT=4",
      "DEFAULT_JOURNAL_SIZE_LIMIT=-1",
      "DEFAULT_MMAP_SIZE=0",
      "DEFAULT_PAGE_SIZE=4096",
      "DEFAULT_PCACHE_INITSZ=20",
      "DEFAULT_RECURSIVE_TRIGGERS",
      "DEFAULT_SECTOR_SIZE=4096",
      "DEFAULT_SYNCHRONOUS=2",
      "DEFAULT_WAL_AUTOCHECKPOINT=1000",
      "DEFAULT_WAL_SYNCHRONOUS=2",
      "DEFAULT_WORKER_THREADS=0",
      "ENABLE_COLUMN_METADATA",
      "ENABLE_DBSTAT_VTAB",
      "ENABLE_FTS3",
      "ENABLE_FTS3_PARENTHESIS",
      "ENABLE_FTS3_TOKENIZER",
      "ENABLE_FTS4",
      "ENABLE_FTS5",
      "ENABLE_LOAD_EXTENSION",
      "ENABLE_MATH_FUNCTIONS",
      "ENABLE_PREUPDATE_HOOK",
      "ENABLE_RTREE",
      "ENABLE_SESSION",
      "ENABLE_STMTVTAB",
      "ENABLE_UNLOCK_NOTIFY",
      "ENABLE_UPDATE_DELETE_LIMIT",
      "HAVE_ISNAN",
      "LIKE_DOESNT_MATCH_BLOBS",
      "MALLOC_SOFT_LIMIT=1024",
      "MAX_ATTACHED=10",
      "MAX_COLUMN=2000",
      "MAX_COMPOUND_SELECT=500",
      "MAX_DEFAULT_PAGE_SIZE=32768",
      "MAX_EXPR_DEPTH=1000",
      "MAX_FUNCTION_ARG=127",
      "MAX_LENGTH=1000000000",
      "MAX_LIKE_PATTERN_LENGTH=50000",
      "MAX_MMAP_SIZE=0x7fff0000",
      "MAX_PAGE_COUNT=1073741823",
      "MAX_PAGE_SIZE=65536",
      "MAX_SCHEMA_RETRY=25",
      "MAX_SQL_LENGTH=1000000000",
      "MAX_TRIGGER_DEPTH=1000",
      "MAX_VARIABLE_NUMBER=250000",
      "MAX_VDBE_OP=250000000",
      "MAX_WORKER_THREADS=8",
      "MUTEX_PTHREADS",
      "OMIT_LOOKASIDE",
      "SECURE_DELETE",
      "SOUNDEX",
      "SYSTEM_MALLOC",
      "TEMP_STORE=1",
      "THREADSAFE=1",
      "USE_URI"
    ],
    "pragmas": {
      "temp_store": 0,
      "cache_size": -2000,
      "synchronous": 2,
      "page_size": 4096
    }
  },
  "cpu": {
    "logical_count": 2,
    "affinity": [
      0,
      1
    ],
    "model": "Intel(R) Xeon(R) CPU @ 2.20GHz"
  },
  "memory": {
    "MemTotal": "993232 kB",
    "MemFree": "300420 kB",
    "MemAvailable": "626760 kB",
    "Buffers": "26484 kB",
    "Cached": "401020 kB",
    "SwapCached": "47128 kB",
    "Active": "296132 kB",
    "Inactive": "257772 kB",
    "Active(anon)": "53184 kB",
    "Inactive(anon)": "73368 kB",
    "Active(file)": "242948 kB",
    "Inactive(file)": "184404 kB",
    "Unevictable": "0 kB",
    "Mlocked": "0 kB",
    "SwapTotal": "4194300 kB",
    "SwapFree": "3997844 kB",
    "Dirty": "540 kB",
    "Writeback": "0 kB",
    "AnonPages": "118972 kB",
    "Mapped": "99616 kB",
    "Shmem": "124 kB",
    "KReclaimable": "52812 kB",
    "Slab": "82176 kB",
    "SReclaimable": "52812 kB",
    "SUnreclaim": "29364 kB",
    "KernelStack": "3040 kB",
    "PageTables": "4148 kB",
    "SecPageTables": "0 kB",
    "NFS_Unstable": "0 kB",
    "Bounce": "0 kB",
    "WritebackTmp": "0 kB",
    "CommitLimit": "4690916 kB",
    "Committed_AS": "1227152 kB",
    "VmallocTotal": "34359738367 kB",
    "VmallocUsed": "12876 kB",
    "VmallocChunk": "0 kB",
    "Percpu": "920 kB",
    "AnonHugePages": "26624 kB",
    "ShmemHugePages": "0 kB",
    "ShmemPmdMapped": "0 kB",
    "FileHugePages": "0 kB",
    "FilePmdMapped": "0 kB",
    "HugePages_Total": "0",
    "HugePages_Free": "0",
    "HugePages_Rsvd": "0",
    "HugePages_Surp": "0",
    "Hugepagesize": "2048 kB",
    "Hugetlb": "0 kB",
    "DirectMap4k": "103224 kB",
    "DirectMap2M": "942080 kB",
    "DirectMap1G": "0 kB"
  },
  "cgroup": {
    "self": "0::/user.slice/user-1000.slice/session-21407.scope",
    "cpu_max": "",
    "memory_max": "",
    "memory_current": ""
  },
  "system": {
    "loadavg": "0.24 0.07 0.02 1/189 1039336",
    "uptime": "2416923.22 3929649.72",
    "dmi_product": "Google Compute Engine",
    "cwd_mount": "/dev/sda1 ext4   rw,relatime,discard,errors=remount-ro"
  },
  "gateway_service": {
    "show": "MainPID=0\nActiveState=inactive",
    "cat": "No files found for hermes-gateway.service."
  },
  "disk": {
    "cwd": {
      "path": "/home/skywind5487/hermes-benchmark/lineage-gate-9425ffa2/research/session-lineage/benchmark",
      "total": 31461457920,
      "used": 24034385920,
      "free": 6021222400
    },
    "tmp": {
      "path": "/tmp",
      "total": 31461457920,
      "used": 24034385920,
      "free": 6021222400
    }
  },
  "git": {
    "root": "/home/skywind5487/hermes-benchmark/lineage-gate-9425ffa2",
    "head": "444c161218b00166bda73f2ec5a21f250e2049bc",
    "branch": "research/session-lineage-benchmark-gate",
    "status_porcelain": ""
  },
  "environment": {}
}
```

## CPU precondition

```json
{
  "requested_seconds": 35.0,
  "wall_seconds": 35.162353592924774,
  "process_cpu_seconds": 35.140228325,
  "iterations": 203000000,
  "checksum": 3653698104
}
```

## Suite metadata

```json
{
  "quick": false,
  "production_db_opened": false,
  "decision_algorithms": [
    "per_seed_no_memo",
    "python_dict_memo"
  ],
  "reference_algorithms": [
    "pure_temp_reference",
    "fixed3_reference"
  ],
  "performance_rows": 16,
  "safety_rows": 36,
  "normal_budget": 10000,
  "precondition_seconds": 35.0,
  "timing_regime": "post-CPU-precondition; intended sustained e2-micro regime",
  "note": "Final production B is intentionally not selected by this runner; focused_budget contains only the two production decision candidates and must be analyzed separately from normal/historical performance."
}
```

## Test output

```text
test_correctness_fixtures_match_reference (test_benchmark_gate.AlgorithmContractTests.test_correctness_fixtures_match_reference) ... ok
test_fixed_plan_has_no_child_full_scan (test_benchmark_gate.AlgorithmContractTests.test_fixed_plan_has_no_child_full_scan) ... ok
test_global_budget_never_creates_fake_root (test_benchmark_gate.AlgorithmContractTests.test_global_budget_never_creates_fake_root) ... ok
test_small_c_candidates_are_unique_and_exact (test_benchmark_gate.AlgorithmContractTests.test_small_c_candidates_are_unique_and_exact) ... ok
test_profiler_handles_tail_into_positive_cycle (test_benchmark_gate.ProductionProfileSafetyTests.test_profiler_handles_tail_into_positive_cycle) ... ok
test_profiler_is_read_only_and_classifies_topology (test_benchmark_gate.ProductionProfileSafetyTests.test_profiler_is_read_only_and_classifies_topology) ... ok
test_profiler_rejects_wrong_hash (test_benchmark_gate.ProductionProfileSafetyTests.test_profiler_rejects_wrong_hash) ... ok
test_focused_scenarios_match_reference (test_focused_gate.FocusedGateTests.test_focused_scenarios_match_reference) ... ok
test_python_memo_budget_fails_closed (test_focused_gate.FocusedGateTests.test_python_memo_budget_fails_closed) ... ok
test_python_memo_reuses_historical_ancestry (test_focused_gate.FocusedGateTests.test_python_memo_reuses_historical_ancestry) ... ok
test_real_historical_fixture_is_depth14_size15 (test_focused_gate.FocusedGateTests.test_real_historical_fixture_is_depth14_size15) ... ok

----------------------------------------------------------------------
Ran 11 tests in 1.432s

OK
```

## focused_gate.csv

```csv
workload_class,scenario,description,candidates_input,k,algorithm,filler,budget,mean_ms,median_ms,p95_ms,stdev_ms,min_ms,max_ms,exact,work,bound_hit,statements,candidates,temp_peak_bytes,rss_kb_before,rss_kb_after,maxrss_kb
normal,focused_normal_roots_c300_k3,Hermes-normal depth0: distinct roots; candidate-level early stop after rank 3,300,3,per_seed_no_memo,20000,10000,0.05615155555555556,0.03746,0.13566039999999996,0.047445251435759825,0.034588,0.18923,True,3,False,6,3,0,22056,22056,22056
normal,focused_normal_roots_c300_k3,Hermes-normal depth0: distinct roots; candidate-level early stop after rank 3,300,3,python_dict_memo,20000,10000,0.03942277777777778,0.038469,0.047765999999999996,0.004733627520852569,0.034894,0.050262,True,3,False,6,3,0,22992,22992,22992
normal,focused_normal_roots_c300_k3,Hermes-normal depth0: distinct roots; candidate-level early stop after rank 3,300,3,pure_temp_reference,20000,10000,0.29960355555555557,0.294199,0.3269762,0.018695890969295963,0.277442,0.327245,True,3,False,11,3,0,22992,22992,22992
normal,focused_normal_roots_c300_k3,Hermes-normal depth0: distinct roots; candidate-level early stop after rank 3,300,3,fixed3_reference,20000,10000,0.3096931111111111,0.297606,0.3898082,0.04514368640351345,0.25746,0.395095,True,5,False,1,5,0,31048,31048,31048
normal,focused_normal_depth1_c300_k3,Hermes-normal observed positive ancestry ceiling: independent depth1; K at rank 3,300,3,per_seed_no_memo,20000,10000,0.055488333333333334,0.051469,0.07481299999999999,0.01152113078940894,0.049116,0.087727,True,6,False,9,3,0,31216,31216,31216
normal,focused_normal_depth1_c300_k3,Hermes-normal observed positive ancestry ceiling: independent depth1; K at rank 3,300,3,python_dict_memo,20000,10000,0.05981911111111111,0.053704,0.08810759999999998,0.0172434041846888,0.050255,0.10803,True,6,False,9,3,0,31216,31216,31216
normal,focused_normal_depth1_c300_k3,Hermes-normal observed positive ancestry ceiling: independent depth1; K at rank 3,300,3,pure_temp_reference,20000,10000,0.34790377777777776,0.335803,0.427577,0.043496678608264075,0.30894,0.447027,True,6,False,17,3,0,31216,31216,31216
normal,focused_normal_depth1_c300_k3,Hermes-normal observed positive ancestry ceiling: independent depth1; K at rank 3,300,3,fixed3_reference,20000,10000,0.32686488888888887,0.320288,0.4029928,0.04146497834658402,0.275428,0.405116,True,10,False,1,5,0,31216,31216,31216
historical_compatibility,focused_historical_depth14_size15_worst_k3,Real frozen-corpus extreme (depth14/size15), deepest-to-root before two roots; maximizes repeated ancestry before K=3,17,3,per_seed_no_memo,20000,10000,13.530040555555556,0.592984,116.8731796,38.52501675150436,0.535223,116.988587,True,122,False,125,17,0,31336,31336,31336
historical_compatibility,focused_historical_depth14_size15_worst_k3,Real frozen-corpus extreme (depth14/size15), deepest-to-root before two roots; maximizes repeated ancestry before K=3,17,3,python_dict_memo,20000,10000,0.1278778888888889,0.12484,0.1576062,0.0167034610445784,0.104878,0.17103,True,17,False,20,17,0,31336,31336,31336
historical_compatibility,focused_historical_depth14_size15_worst_k3,Real frozen-corpus extreme (depth14/size15), deepest-to-root before two roots; maximizes repeated ancestry before K=3,17,3,pure_temp_reference,20000,10000,13.64070288888889,0.564849,118.37665139999998,39.27133304145264,0.487567,118.493082,True,17,False,53,17,0,31336,31336,31336
historical_compatibility,focused_historical_depth14_size15_worst_k3,Real frozen-corpus extreme (depth14/size15), deepest-to-root before two roots; maximizes repeated ancestry before K=3,17,3,fixed3_reference,20000,10000,0.42620455555555553,0.423345,0.4921374,0.04288752269477639,0.373369,0.512495,True,17,False,1,7,0,31336,31336,31336
historical_compatibility,focused_historical_depth14_size15_fullconsume_k3,Real frozen-corpus extreme full-consume adversary: all 15 members of one lineage; K=3 unreachable,15,3,per_seed_no_memo,20000,10000,0.6612413333333333,0.619249,0.9716016,0.15865459766662746,0.522406,0.984158,True,120,False,123,15,0,31432,31432,31432
historical_compatibility,focused_historical_depth14_size15_fullconsume_k3,Real frozen-corpus extreme full-consume adversary: all 15 members of one lineage; K=3 unreachable,15,3,python_dict_memo,20000,10000,0.11537077777777777,0.114357,0.1269402,0.007018152414678347,0.106974,0.128046,True,15,False,18,15,0,31432,31432,31432
historical_compatibility,focused_historical_depth14_size15_fullconsume_k3,Real frozen-corpus extreme full-consume adversary: all 15 members of one lineage; K=3 unreachable,15,3,pure_temp_reference,20000,10000,0.5296715555555556,0.51964,0.5775168,0.030808314428793736,0.49581,0.584569,True,15,False,49,15,0,31432,31432,31432
historical_compatibility,focused_historical_depth14_size15_fullconsume_k3,Real frozen-corpus extreme full-consume adversary: all 15 members of one lineage; K=3 unreachable,15,3,fixed3_reference,20000,10000,0.39430166666666666,0.386486,0.4507308,0.03867632711672586,0.350789,0.462659,True,15,False,1,5,0,31432,31432,31432
```

## focused_budget.csv

```csv
workload_class,scenario,description,candidates_input,k,algorithm,budget,mean_ms,median_ms,p95_ms,stdev_ms,min_ms,max_ms,exact,work,bound_hit,statements,candidates,temp_peak_bytes,rss_kb_before,rss_kb_after,maxrss_kb
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,64,0.4395945,0.4395945,0.48534635,0.05083401460046828,0.38876,0.490429,False,64,True,67,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,64,0.4459005,0.4459005,0.50406685,0.06462980174576333,0.381271,0.51053,False,64,True,67,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,128,0.802489,0.802489,0.8935132,0.1011383737982285,0.701351,0.903627,False,128,True,131,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,128,0.9155805,0.9155805,1.01661685,0.11226292571716345,0.803318,1.027843,False,128,True,131,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,256,1.4392205,1.4392205,1.57643165,0.15245675555293844,1.286763,1.591678,False,256,True,259,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,256,1.4956745,1.4956745,1.69949445,0.22646616774997853,1.269208,1.722141,False,256,True,259,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,512,3.4593395,3.4593395,4.15884795,0.7772314323872699,2.682108,4.236571,False,512,True,515,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,512,2.8091175,2.8091175,2.92325235,0.12681656334005636,2.682301,2.935934,False,512,True,515,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,1000,5.0486535,5.0486535,5.07330855,0.027395082449778128,5.021258,5.076049,False,1000,True,1003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,1000,115.3792055,115.3792055,214.23339515,109.8379882275437,5.541217,225.217194,False,1000,True,1003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,1500,7.498719,7.498719,7.5717631,0.0811601183501156,7.417559,7.579879,False,1500,True,1503,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,1500,117.367458,117.367458,216.4395507,110.08010262283332,7.287355,227.447561,False,1500,True,1503,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,2000,127.2070265,127.2070265,226.36928805,110.18029032940069,17.026736,237.387317,False,2000,True,2003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,2000,127.293141,127.293141,226.4323719,110.15470055872816,17.13844,237.447842,False,2000,True,2003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,5000,375.7321295,375.7321295,476.23107495,111.6654958834302,264.066634,487.397625,False,5000,True,5003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,5000,264.501572,264.501572,264.5874032,0.0953681956744507,264.406204,264.59694,False,5000,True,5003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,per_seed_no_memo,10000,743.904967,743.904967,746.6665099,3.068380636450216,740.836586,746.973348,False,10000,True,10003,1,0,31448,31448,31448
safety_only,path_depth10000_single,10k-hop acyclic chain; safety/B cost curve,1,1,python_dict_memo,10000,748.4015935,748.4015935,749.2141706500001,0.9028631875346387,747.49873,749.304457,False,10000,True,10003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,64,0.7301325,0.7301325,0.80829915,0.0868514965189598,0.643281,0.816984,False,64,True,67,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,64,0.7160015,0.7160015,0.80650415,0.10055811091683057,0.615443,0.81656,False,64,True,67,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,128,1.295715,1.295715,1.3885518,0.10315248419082977,1.192563,1.398867,False,128,True,131,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,128,1.538634,1.538634,1.643997,0.11706981487641998,1.421564,1.655704,False,128,True,131,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,256,2.2594395,2.2594395,2.53502625,0.3062069540404654,1.953232,2.565647,False,256,True,259,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,256,2.1071165,2.1071165,2.24377935,0.15184828615706247,1.955269,2.258964,False,256,True,259,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,512,4.019333,4.019333,4.059591,0.04473111433562669,3.974602,4.064064,False,512,True,515,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,512,227.8769085,227.8769085,231.10399845,3.5856538213397315,224.291254,231.462563,False,512,True,515,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,1000,117.50414,117.50414,216.2589548,109.72757189472851,7.776568,227.231712,False,1000,True,1003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,1000,226.591901,226.591901,226.895588,0.33743044534394395,226.254471,226.929331,False,1000,True,1003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,1500,11.22244,11.22244,11.3538311,0.14598885355795647,11.076451,11.368429,False,1500,True,1503,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,1500,10.90575,10.90575,11.0483337,0.15842622273846615,10.747324,11.064177,False,1500,True,1503,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,2000,234.7452355,234.7452355,235.04497585,0.3330440129701599,234.412191,235.07828,False,2000,True,2003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,2000,125.6611,125.6611,225.1707727,110.56630259005223,15.094797,236.227403,False,2000,True,2003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,5000,370.6524925,370.6524925,465.80681095,105.72702007110451,264.925472,476.379513,False,5000,True,5003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,5000,478.8326125,478.8326125,478.97587735,0.15918315641415823,478.67343,478.991795,False,5000,True,5003,1,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,per_seed_no_memo,10000,742.9292175,742.9292175,746.40073765,3.857245784110125,739.071972,746.786463,True,10000,True,10003,2,0,31448,31448,31448
safety_only,path_depth5000_concentrated_c300,5k-hop single lineage with C=300; long + concentrated stress,300,3,python_dict_memo,10000,260.679962,260.679962,263.9868428,3.6743118976851994,257.00565,264.354274,True,5001,False,5004,300,0,31448,31448,31448
```
