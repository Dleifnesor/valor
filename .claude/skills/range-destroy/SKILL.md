---
name: range-destroy
description: Tear down a VALOR test range (deletes only that range's VMs). Use when the user asks to destroy, remove, delete or tear down a range, or runs /range-destroy <name>.
---

# Destroy a range

1. Identify the range name (argument, or ask). Call `cluster_info` and confirm the range exists; show its VMs
   (host, VMID, state) from `ranges.<name>.hosts`.
2. Tell the user exactly which VMs will be deleted and that this cannot be undone (the spec file stays, so the
   range can be rebuilt with /range-build). **Ask for explicit confirmation.**
3. Call `range_destroy(range=<name>)` and poll `job_status` until it finishes.
4. Report the removed VMs. The engine only deletes VMs in its own pool that carry the tag `valor-range-<name>`;
   nothing else on the cluster can be touched.
5. Keep `ranges/<name>.yaml` and `journals/<name>.md` unless the user asks to delete them too.
