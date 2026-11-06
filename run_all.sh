POLICY="solution.policies.baseline"
MODULE_NAME="${POLICY##*.}"

for DAY in $(seq -f "day_%03g" 1 20); do
  echo "--- Processing ${DAY} ---"
  python3 -m simulator --policy "${POLICY}" --data data --day "${DAY}" --output "results/${MODULE_NAME}/${DAY}"
  python3 -m solution.performance_monitor --data data --day "${DAY}" --log "results/${MODULE_NAME}/${DAY}/log.txt"
done

python3 -m solution.summarize_results