# *hands over the pen* OwO roles/custom-script/role.sh: runs the spec's OWN bash script with all of VALOR's helpers
# available. The script is shown in full in the plan before it runs ^w^
# Role: custom-script - runs the spec's own bash script with VALOR's helpers. Params: VALOR_PARAM_SCRIPT, _PORTS
# An empty (or whitespace-only) script is a mistake UwU
[ -n "${VALOR_PARAM_SCRIPT//[[:space:]]/}" ] || { echo "the script is empty" >&2; exit 1; }
# Ports must be plain numbers >w<
for p in ${VALOR_PARAM_PORTS:-}; do [[ "$p" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$p'" >&2; exit 1; }; done
# A marker in the output so the log shows where the custom part starts OwO
echo "---- custom script on ${VALOR_HOST} ----"
# *takes a deep breath* run the script itself, in this same shell (so it can use the helpers and set
# VALOR_CHANGED) ^w^
eval "$VALOR_PARAM_SCRIPT"
# VALOR can't know what the script did, so it always counts as a change UwU
changed
# Open each declared port in firewalld and wait up to a minute for it *wags tail*
for p in ${VALOR_PARAM_PORTS:-}; do
  firewall_open "$p"
  wait_port "$p" 60
done
