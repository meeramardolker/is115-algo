for POLICY in byregion byregion_fill_50 byregion_fill_80 byregion_fill_100; do
  for DAY in $(seq -f "day_%03g" 1 20); do
    python3 -m simulator --policy solution.policies.$POLICY --data data --day $DAY --output results/$POLICY/$DAY
    python3 -m solution.performance_monitor --data data --day $DAY --log results/$POLICY/$DAY/log.txt
  done
done

python3 -m solution.summarise_results