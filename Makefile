# Cell #1 pipeline orchestrator — Jackson-databind × correctness.
# See phase-0-scope.md §2 for the design.
#
# Usage:
#   make           — advance pipeline; runs all auto steps until a human step
#   make status    — show pipeline state and the next action
#   make help      — list all targets
#
# Each rule produces a file (the artifact). When a target depends on a file
# that requires human action, the recipe prints instructions and exits 1.
# Re-run `make` after doing the human work.

SHELL := /bin/bash

# P0-2: relocatable — derive project root from Makefile location.
PROJ    := $(patsubst %/,%,$(dir $(abspath $(firstword $(MAKEFILE_LIST)))))
SCRIPTS := $(PROJ)/scripts
CELL    := $(PROJ)/cell-1

# Prefer the project venv interpreter (it has pyyaml + the optional deps);
# fall back to system python3 if the venv hasn't been created yet. The scripts
# import yaml, so bare `python3` fails with ModuleNotFoundError unless the venv
# is on PATH — resolve it here so `make` works without activating the venv.
PYTHON  := $(if $(wildcard $(PROJ)/.venv/bin/python),$(PROJ)/.venv/bin/python,python3)

# --- ANSI for human-step banners ---
B := \033[1m
Y := \033[33m
R := \033[31m
D := \033[2m
N := \033[0m

# --- canonical sentinel for the validation-scaffolds-generated state ---
SCAFFOLDS_SENTINEL := $(CELL)/hunt/validation/.scaffolds-generated

.PHONY: all status help recon backtest-candidates backtest-prep backtest-score \
        hunt-prep hunt-validate-scaffold hunt-validate-report pass23-prep final-report \
        clean clean-worktrees reset-pass23 reset-hunt reset-backtest reset-cell wipe \
        dashboard targets \
        day1-recon day2-build day2-backtest-prep day2-score \
        day3-hunt-prep day3-scaffolds day3-report \
        day4-prep day4-report

.DEFAULT_GOAL := all

# === Top-level ===
all: $(CELL)/cell-1-report.md

status:
	@$(SCRIPTS)/_status.sh

dashboard:
	@printf "$(B)[dashboard]$(N) starting at http://127.0.0.1:8765 (Ctrl-C to stop)\n"
	@$(PROJ)/.venv/bin/python $(PROJ)/tool/server.py

targets:
	@printf "$(B)[targets]$(N) materializing synthetic demo targets from targets/_src/\n"
	@$(PYTHON) $(PROJ)/tool/demo_targets.py

help:
	@printf "$(B)Cell #1 pipeline$(N)\n\n"
	@printf "  $(B)make$(N)           advance to next step (auto steps chain; stops at human steps)\n"
	@printf "  $(B)make status$(N)    show where you are in the pipeline\n"
	@printf "  $(B)make dashboard$(N) start the FastAPI dashboard at http://127.0.0.1:8765\n"
	@printf "  $(B)make targets$(N)   materialize synthetic demo targets (targets/_src -> git repos)\n"
	@printf "  $(B)make help$(N)      this\n\n"
	@printf "$(B)Jump to a specific step$(N) (skips dependency checks)\n"
	@printf "  $(D)# friendly name$(N)              $(D)# canonical name$(N)        $(D)# what it does$(N)\n"
	@printf "  make recon                  (day1-recon)            Day 1 — clone + baseline scanners + recon stub\n"
	@printf "  make backtest-candidates    (day2-build)            Day 2 — rank candidates, emit autopick\n"
	@printf "  make backtest-prep          (day2-backtest-prep)    Day 2 — worktrees + per-entry prompts\n"
	@printf "  make backtest-score         (day2-score)            Day 2 — score backtest, write report\n"
	@printf "  make hunt-prep              (day3-hunt-prep)        Day 3 — substitute shortlist into prompts\n"
	@printf "  make hunt-validate-scaffold (day3-scaffolds)        Day 3 — generate validation scaffolds\n"
	@printf "  make hunt-validate-report   (day3-report)           Day 3 — aggregate scaffolds → pass-1 report\n"
	@printf "  make pass23-prep            (day4-prep)             Day 4 — pass-2/3 stubs\n"
	@printf "  make final-report           (day4-report)           Day 4 — self-consistency + cell-1-report.md\n"
	@printf "$(D)  (Canonical names match tool/pipeline.py::PIPELINE ids — single source of truth, P0-5/P1-10 fix.)$(N)\n"
	@printf "\n$(B)Reset / re-run$(N) (destructive — removes artifacts so a phase re-runs)\n"
	@printf "  make reset-pass23           remove Day 4 work only (keeps pass-1 candidates)\n"
	@printf "  make reset-hunt             remove Day 3 + Day 4 (keeps backtest results)\n"
	@printf "  make reset-backtest         remove Day 2 + Day 3 + Day 4 (keeps recon + shortlist)\n"
	@printf "  make reset-cell             remove all cell-1/ (keeps jackson-databind clone)\n"
	@printf "  make clean                  alias for reset-cell\n"
	@printf "  make wipe                   also remove targets/jackson-databind (~500MB re-clone)\n"

# =============================================================================
# Day 1 — recon (auto) → Explore inventory (human) → shortlist (human)
# =============================================================================

$(CELL)/recon/cell-1-recon.md:
	@printf "$(B)[day1]$(N) running recon\n"
	$(SCRIPTS)/day1-recon.sh
recon: $(CELL)/recon/cell-1-recon.md

$(CELL)/recon/explore-inventory.md: $(CELL)/recon/cell-1-recon.md
	@printf "\n$(Y)═══ HUMAN STEP — Day 1: Explore inventory ═══$(N)\n"
	@printf "Drive the Explore subagent in Claude Code with this prompt:\n"
	@printf "  $(D)cat $(SCRIPTS)/explore-prompt.md$(N)\n"
	@printf "Use the prompt body section. The subagent's response IS the inventory markdown.\n"
	@printf "Save its message body to:\n"
	@printf "  $(B)$(CELL)/recon/explore-inventory.md$(N)\n"
	@printf "Then: $(B)make$(N)\n\n"
	@exit 1

$(CELL)/shortlist.txt: $(CELL)/recon/explore-inventory.md
	@printf "\n$(Y)═══ HUMAN STEP — Day 1: shortlist ═══$(N)\n"
	@printf "Pick 3-8 hot-spot files for the novel hunt. Sources:\n"
	@printf "  - $(CELL)/recon/hot-spots-coarse.txt\n"
	@printf "  - $(CELL)/recon/explore-inventory.md\n"
	@printf "  - $(CELL)/recon/deserializer-inventory.txt\n"
	@printf "Write one repo-relative file path per line to:\n"
	@printf "  $(B)$(CELL)/shortlist.txt$(N)\n"
	@printf "Then: $(B)make$(N)\n\n"
	@exit 1

# =============================================================================
# Day 2 — build candidates (auto) → finalize dataset (human) → backtest
# =============================================================================

$(CELL)/backtest/candidates.yaml: $(CELL)/shortlist.txt
	@printf "$(B)[day2]$(N) ranking backtest candidates\n"
	$(PYTHON) $(SCRIPTS)/day2-build-dataset.py
backtest-candidates: $(CELL)/backtest/candidates.yaml

# Use a marker target since dataset.yaml exists in stub form right after
# build-dataset.py runs, but is not "finalized" until expected_subagent is set.
$(CELL)/backtest/.dataset.ok: $(CELL)/backtest/candidates.yaml
	@if $(PYTHON) $(SCRIPTS)/_check.py dataset-finalized; then \
	  touch $@; \
	else \
	  printf "\n$(Y)═══ HUMAN STEP — Day 2: finalize dataset ═══$(N)\n"; \
	  printf "Option A (accept top-10 auto-pick):\n"; \
	  printf "  $(D)cp $(CELL)/backtest/dataset-autopick.yaml $(CELL)/backtest/dataset.yaml$(N)\n"; \
	  printf "Option B: hand-pick 10 from $(CELL)/backtest/candidates.yaml.\n\n"; \
	  printf "Then edit $(B)$(CELL)/backtest/dataset.yaml$(N): fill $(B)expected_subagent$(N)\n"; \
	  printf "(code-quality | edge-case) and $(B)notes$(N) per entry.\n"; \
	  printf "Then: $(B)make$(N)\n\n"; \
	  exit 1; \
	fi

$(CELL)/backtest/runbook.md: $(CELL)/backtest/.dataset.ok
	@printf "$(B)[day2]$(N) preparing backtest worktrees + prompts\n"
	$(PYTHON) $(SCRIPTS)/day2-backtest.py prepare
backtest-prep: $(CELL)/backtest/runbook.md

# P0-1: labels are advisory only — the gate runs on deterministic file-coverage,
# not on labels.yaml. So .runs.ok gates on findings being populated; labeling is
# optional triage the human MAY do but is never required to advance.
$(CELL)/backtest/.runs.ok: $(CELL)/backtest/runbook.md
	@if $(PYTHON) $(SCRIPTS)/_check.py backtest-runs-populated; then \
	  touch $@; \
	else \
	  printf "\n$(Y)═══ HUMAN STEP — Day 2: run backtest ═══$(N)\n"; \
	  printf "See $(B)$(CELL)/backtest/runbook.md$(N) for the per-entry checklist.\n\n"; \
	  printf "For EACH of 10 entries:\n"; \
	  printf "  1. Run $(CELL)/backtest/runs/<issue>/prompt.md via a fresh Agent\n"; \
	  printf "     (subagent_type: general-purpose or code-reviewer)\n"; \
	  printf "  2. Paste the agent's $(D)findings:$(N) YAML into findings.yaml\n"; \
	  printf "  $(D)(optional)$(N) Label findings in labels.yaml (matches_known | unrelated_tp |\n"; \
	  printf "     fp | dupe_of_baseline) for richer triage — advisory only, never gates.\n\n"; \
	  printf "Then: $(B)make$(N)\n\n"; \
	  exit 1; \
	fi

$(CELL)/cell-1-backtest.md: $(CELL)/backtest/.runs.ok
	@printf "$(B)[day2]$(N) scoring backtest\n"
	$(PYTHON) $(SCRIPTS)/day2-backtest.py score
backtest-score: $(CELL)/cell-1-backtest.md

# =============================================================================
# Day 3 — hunt prep (auto) → run prompts (human) → validate (state-aware)
# =============================================================================

$(CELL)/hunt/code-quality/prompt.md: $(CELL)/shortlist.txt
	@printf "$(B)[day3]$(N) generating novel-hunt prompts\n"
	$(PYTHON) $(SCRIPTS)/day3-hunt.py prepare
$(CELL)/hunt/edge-case/prompt.md: $(CELL)/hunt/code-quality/prompt.md ;
hunt-prep: $(CELL)/hunt/code-quality/prompt.md $(CELL)/hunt/edge-case/prompt.md

# Note: hunt-prep is GATED on Day-2 backtest passing.
# We can't check the gate decision automatically (it's a markdown table), so
# we depend on cell-1-backtest.md existing and trust the human to abort if
# the gate decision was KILL.
$(CELL)/hunt/.gate.ok: $(CELL)/cell-1-backtest.md
	@printf "\n$(Y)Gate check: did Day-2 backtest PASS the Phase-0 gate?$(N)\n"
	@printf "Review $(B)$(CELL)/cell-1-backtest.md$(N) — the 'Gate decision' section.\n"
	@printf "If it says PROCEED, run: $(B)touch $@ && make$(N)\n"
	@printf "If it says KILL, follow scope §6 (run a retry cell or write post-mortem).\n\n"
	@exit 1

$(CELL)/hunt/code-quality/findings-pass1.yaml: $(CELL)/hunt/.gate.ok $(CELL)/hunt/code-quality/prompt.md
	@true  # created by hunt-prep as a stub; this rule just enforces the gate ordering

$(CELL)/hunt/.pass1.ok: $(CELL)/hunt/code-quality/findings-pass1.yaml
	@if $(PYTHON) $(SCRIPTS)/_check.py hunt-findings-populated; then \
	  touch $@; \
	else \
	  printf "\n$(Y)═══ HUMAN STEP — Day 3: run novel-hunt prompts ═══$(N)\n"; \
	  printf "Run EACH prompt in a $(B)fresh$(N) Agent (subagent_type: general-purpose):\n\n"; \
	  printf "  $(D)cat $(CELL)/hunt/code-quality/prompt.md$(N)\n"; \
	  printf "  $(D)cat $(CELL)/hunt/edge-case/prompt.md$(N)\n\n"; \
	  printf "Paste each agent's $(D)findings:$(N) YAML block into:\n"; \
	  printf "  $(B)$(CELL)/hunt/code-quality/findings-pass1.yaml$(N)\n"; \
	  printf "  $(B)$(CELL)/hunt/edge-case/findings-pass1.yaml$(N)\n\n"; \
	  printf "If an agent returned $(D)findings: []$(N), leave that as-is (it's a valid result).\n"; \
	  printf "Then: $(B)make$(N)\n\n"; \
	  exit 1; \
	fi

# Day-3 validate is state-aware: first run generates scaffolds, second writes report.
$(SCAFFOLDS_SENTINEL): $(CELL)/hunt/.pass1.ok
	@printf "$(B)[day3]$(N) generating validation scaffolds + auto-dedup\n"
	$(PYTHON) $(SCRIPTS)/day3-hunt.py validate
	@touch $@
hunt-validate-scaffold: $(SCAFFOLDS_SENTINEL)

$(CELL)/hunt/.gates.ok: $(SCAFFOLDS_SENTINEL)
	@if $(PYTHON) $(SCRIPTS)/_check.py hunt-gates-filled; then \
	  touch $@; \
	else \
	  printf "\n$(Y)═══ HUMAN STEP — Day 3: fill validation gates ═══$(N)\n"; \
	  printf "For each scaffold under $(CELL)/hunt/validation/<id>.yaml:\n"; \
	  printf "  1. Write reproducer JUnit test → $(CELL)/hunt/repros/<id>.java; run; fill $(D)reproducer$(N)\n"; \
	  printf "  2. Review auto-dedup OSV/GitHub matches; fill $(D)dedup$(N)\n"; \
	  printf "  3. Assign CWE; fill $(D)cwe$(N)\n"; \
	  printf "  4. Write fix patch; run $(D)mvn test$(N); fill $(D)fix_passes_tests$(N)\n"; \
	  printf "  5. Set $(B)final_status$(N): validated | unreproducible | dupe | false-positive\n\n"; \
	  printf "Then: $(B)make$(N)\n\n"; \
	  exit 1; \
	fi

$(CELL)/cell-1-candidates-pass1.md: $(CELL)/hunt/.gates.ok
	@printf "$(B)[day3]$(N) aggregating pass-1 candidates → report\n"
	$(PYTHON) $(SCRIPTS)/day3-hunt.py validate
hunt-validate-report: $(CELL)/cell-1-candidates-pass1.md

# =============================================================================
# Day 4 — pass-2/3 stubs (auto) → run prompts (human) → final report
# =============================================================================

$(CELL)/hunt/code-quality/findings-pass2.yaml: $(CELL)/cell-1-candidates-pass1.md
	@printf "$(B)[day4]$(N) creating pass-2 + pass-3 stubs\n"
	$(PYTHON) $(SCRIPTS)/day4-finalize.py prepare
$(CELL)/hunt/edge-case/findings-pass2.yaml: $(CELL)/hunt/code-quality/findings-pass2.yaml ;
pass23-prep: $(CELL)/hunt/code-quality/findings-pass2.yaml $(CELL)/hunt/edge-case/findings-pass2.yaml

$(CELL)/hunt/.passes23.ok: $(CELL)/hunt/code-quality/findings-pass2.yaml
	@if $(PYTHON) $(SCRIPTS)/_check.py passes23-populated; then \
	  touch $@; \
	else \
	  printf "\n$(Y)═══ HUMAN STEP — Day 4: pass-2 + pass-3 hunts ═══$(N)\n"; \
	  printf "Re-run the $(B)same$(N) prompt.md against TWO MORE fresh Agents per angle.\n"; \
	  printf "4 total runs:\n"; \
	  printf "  - code-quality pass-2 → $(B)$(CELL)/hunt/code-quality/findings-pass2.yaml$(N)\n"; \
	  printf "  - code-quality pass-3 → $(B)$(CELL)/hunt/code-quality/findings-pass3.yaml$(N)\n"; \
	  printf "  - edge-case    pass-2 → $(B)$(CELL)/hunt/edge-case/findings-pass2.yaml$(N)\n"; \
	  printf "  - edge-case    pass-3 → $(B)$(CELL)/hunt/edge-case/findings-pass3.yaml$(N)\n\n"; \
	  printf "$(R)CRITICAL$(N): each pass must be a $(B)fresh$(N) Agent context (no carryover).\n"; \
	  printf "Then: $(B)make$(N)\n\n"; \
	  exit 1; \
	fi

$(CELL)/cell-1-report.md: $(CELL)/hunt/.passes23.ok
	@printf "$(B)[day4]$(N) self-consistency + final report\n"
	$(PYTHON) $(SCRIPTS)/day4-finalize.py report
final-report: $(CELL)/cell-1-report.md
	@printf "\n$(B)✓ Pipeline complete.$(N) Final report:\n  $(CELL)/cell-1-report.md\n"
	@printf "  Fill the HUMAN sections (cost / lessons / recommendation), then decide.\n\n"

# =============================================================================
# Canonical step-name aliases (P1-10) — each maps to its friendly equivalent.
# Canonical names match `tool/pipeline.py::PIPELINE` ids. Both forms work:
#   make day1-recon   ≡   make recon
#   make day2-build   ≡   make backtest-candidates
# Discoverable via `make help`.
# =============================================================================
day1-recon: recon
day2-build: backtest-candidates
day2-backtest-prep: backtest-prep
day2-score: backtest-score
day3-hunt-prep: hunt-prep
day3-scaffolds: hunt-validate-scaffold
day3-report: hunt-validate-report
day4-prep: pass23-prep
day4-report: final-report

# =============================================================================
# Reset / re-run (destructive — removes artifacts so a phase re-runs)
# =============================================================================

# Internal: detach + prune git worktrees before removing their directories,
# so the main clone's worktree refs don't dangle. Idempotent + no-op if the
# clone isn't there yet.
clean-worktrees:
	@if [ -d "$(CELL)/backtest/worktrees" ] && [ -d "$(PROJ)/targets/jackson-databind/.git" ]; then \
	  for wt in $(CELL)/backtest/worktrees/*; do \
	    [ -d "$$wt" ] || continue; \
	    git -C "$(PROJ)/targets/jackson-databind" worktree remove --force "$$wt" 2>/dev/null || true; \
	  done; \
	  git -C "$(PROJ)/targets/jackson-databind" worktree prune 2>/dev/null || true; \
	  printf "$(D)[reset] pruned git worktrees$(N)\n"; \
	fi

reset-pass23:
	@printf "$(R)[reset]$(N) removing Day 4 outputs (pass-2/3 + final report)\n"
	@rm -f $(CELL)/hunt/code-quality/findings-pass2.yaml
	@rm -f $(CELL)/hunt/code-quality/findings-pass3.yaml
	@rm -f $(CELL)/hunt/edge-case/findings-pass2.yaml
	@rm -f $(CELL)/hunt/edge-case/findings-pass3.yaml
	@rm -f $(CELL)/hunt/.passes23.ok
	@rm -f $(CELL)/cell-1-report.md
	@printf "  next: $(B)make$(N) (Day 4 will re-prep pass-2/3 stubs)\n"

reset-hunt: reset-pass23
	@printf "$(R)[reset]$(N) removing Day 3 (hunt + validation + pass-1 report)\n"
	@rm -rf $(CELL)/hunt
	@rm -f $(CELL)/cell-1-candidates-pass1.md
	@printf "  next: $(B)make$(N) (Day 3 will regenerate prompts from existing shortlist)\n"

reset-backtest: reset-hunt clean-worktrees
	@printf "$(R)[reset]$(N) removing Day 2 (backtest + dataset + score)\n"
	@rm -rf $(CELL)/backtest
	@rm -f $(CELL)/cell-1-backtest.md
	@printf "  next: $(B)make$(N) (Day 2 will rebuild candidates; you'll re-finalize dataset.yaml)\n"

reset-cell: reset-backtest
	@printf "$(R)[reset]$(N) removing all cell-1/ artifacts\n"
	@rm -rf $(CELL)
	@printf "  next: $(B)make$(N) (full Day 1 re-run; jackson-databind clone is kept)\n"

clean: reset-cell

# Total nuke: removes cell-1/ AND the ~500MB jackson-databind clone.
wipe: clean-worktrees
	@printf "$(R)[wipe]$(N) removing cell-1/ AND targets/jackson-databind/ (~500MB clone)\n"
	@rm -rf $(CELL)
	@rm -rf $(PROJ)/targets/jackson-databind
	@printf "  next: $(B)make$(N) (full clean rebuild — clone will take 1-2min)\n"
