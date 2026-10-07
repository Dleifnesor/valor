# Role: custom-script - runs the spec's own bash script with VALOR's helpers. Params: VALOR_PARAM_SCRIPT, _PORTS
[ -n "${VALOR_PARAM_SCRIPT//[[:space:]]/}" ] || { echo "the script is empty" >&2; exit 1; }
for p in ${VALOR_PARAM_PORTS:-}; do [[ "$p" =~ ^[0-9]{1,5}$ ]] || { echo "invalid port '$p'" >&2; exit 1; }; done
echo "---- custom script on ${VALOR_HOST} ----"
eval "$VALOR_PARAM_SCRIPT"
changed
for p in ${VALOR_PARAM_PORTS:-}; do
  firewall_open "$p"
  wait_port "$p" 60
done
