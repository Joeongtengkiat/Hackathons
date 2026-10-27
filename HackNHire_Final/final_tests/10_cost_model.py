"""Cost model (report section 3.6): monthly cost at Indonesia scale from measured sizes and published cloud
prices, using analysis\\cost_model.py with the draft inputs in analysis\\cost_inputs_draft.json.
Recorded result: storage $0.10/month; 1 million checks: reads $1.60 (vs $12.00 reading the original layers);
weekly alert refresh $0.52/month; server $30.95/month; total about $33 (vs $43). Seconds."""
from _common import J, run, tail

run([J("analysis", "cost_model.py"), J("analysis", "cost_inputs_draft.json")], "cost_model.txt", "Cost model, draft inputs")
tail("cost_model.txt", 20)
