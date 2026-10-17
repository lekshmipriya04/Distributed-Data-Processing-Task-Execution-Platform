# Run code or train a model

- **Run File in Parallel…** runs the open file as many tasks at once: one per
  input you type, one per line of a file, or N copies. Pick the language, the
  nodes and the cores per task.
- **Run File with Runtime…** runs it once, in any language your workers have.
- Output opens beside your code and updates live, task by task.
- Right-click an execution to cancel it, run it again (all tasks or only the
  failed ones), or save every task's output into `distributed-results/`.
- **Submit Training Job** trains a built-in model, your own `model.py`, or,
  with the optional Spark + Ray engine, any Spark MLlib model.
