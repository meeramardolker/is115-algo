# IS115 Project README
Reproducing the results
All policies and days at once
bash
`bash run_all.sh`

This runs the simulator and the performance monitor for every policy on day_001 to day_020, then prints a summary table. Results are written to results/<policy>/<day>/.

One policy on one day

Step 1: run the simulation (example: baseline on day 1).

bash
`python3 -m simulator --policy solution.policies.baseline \
    --data data --day day_001 \
    --output results/baseline/day_001`

Step 2: run the performance monitor on the event log.

bash
`python3 -m solution.performance_monitor --data data --day day_001 \
    --log results/baseline/day_001/log.txt`

This writes results/baseline/day_001/metrics.json.

To run another policy, replace baseline with the policy's file name (without .py) in both commands, for example solution.policies.byregion_fill_100 and results/byregion_fill_100/day_001.

Summary table
bash
`python3 -m solution.summarise_results`

This pools the counts over all 20 days for each policy. Add --per-day to also print each day's results.

Project structure
.
├── README.md
├── run_all.sh                   # runs every policy on all 20 days, then the summary
├── data/                        # provided data (unchanged)
├── simulator/                   # provided simulator (unchanged)
├── solution/
│   ├── performance_monitor.py   # on-time rate and secondary metrics
│   ├── summarise_results.py     # pooled summary across days
│   └── policies/
│       ├── baseline.py
│       ├── helpers.py
│       ├── byregion.py
│       ├── byregion_fill_50.py
│       ├── byregion_fill_70.py
│       ├── byregion_fill_100.py
│       ├── edf.py
│       └── savings.py
└── results/
    └── <policy>/<day>/          # log.txt and metrics.json for each run