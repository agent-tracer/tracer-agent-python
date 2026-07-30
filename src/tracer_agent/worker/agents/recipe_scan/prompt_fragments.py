"""계약이 소유한 recipe-scan 프롬프트 조각을 그대로 든다."""

from __future__ import annotations

from ..shared.fragment_registry import build_fragment_registry

# 역할 문장과 조립 순서는 이 백엔드가 쥐고, 역할과 무관한 사실만 여기에 그대로 옮겨 둔다.
FRAGMENTS: dict[str, str] = {
    "recipeDefinition": """\
A recipe preserves one distinct user request, the successful work flow it establishes, and the
friction encountered along the way. One task often holds several unrelated requests, so a task can
yield several recipes. The successful process is valuable; do not mine only for mistakes.
A recipe is a pattern, not a transcript. Remove incidental details, but preserve load-bearing
friction: corrections, non-obvious pitfalls, and evidence eventIds. Capture only steps the trajectory
has already carried out and verified; leave out steps that were attempted but never confirmed to
work, and do not invent a conclusion the task has not reached.""",
    "evidenceSourcing": """\
You do NOT read the ledger yourself. Specialists already read the evidence in their own isolated
contexts, and each reported a verdict plus verbatim excerpts; their evidence ledgers were merged into
one. Your only tool is ${checkCitationsTool}: before you emit final candidates, pass the IDs you intend
to cite and it names the ones the merged ledger cannot back. An ID no specialist surfaced is not
citable, so never guess IDs.""",
    "citationDiscipline": """\
Every ID you cite in your final output (taskIds, turnIds, eventIds, rule IDs, revises_recipe_id) is
checked against the merged specialist ledger. A candidate citing an unbacked ID is rejected; you get
exactly one repair attempt, and output that is still ungrounded after it is dropped, so nothing is
saved. Verify with ${checkCitationsTool} first.
A contributing task counts as supported only when a specialist actually read its events. A task a
specialist merely named without reading is not enough.""",
    "turnSplitting": """\
A turnId marks one user request and everything the agent did to serve it. Split the task by turnId
when its turns pursue unrelated goals, and write one recipe per goal. Merge adjacent turns only when
they carry one intent forward.""",
    "candidateBudget": """\
Return zero recipes if the evidence is too thin, otherwise one candidate per distinct reusable
workflow, up to ${recipeCandidateLimit}.""",
    "redispatchProtocol": """\
When the specialist reports leave a specific, answerable gap that blocks a candidate, you may ask for
one more round of investigation instead of finalizing. Return a redispatch request — a list of up to
${maxRedispatchProbes} {probe, weight, question} entries, where probe is one of timeline, rules, or
repetition — and leave recipes empty; those specialists are dispatched and their reports come back to
you. You get at most ${maxRedispatchRounds} such round; after it, finalize from what you have.
Redispatch only for a gap a specialist can actually close, not to re-read evidence you already hold.
Return either recipes or a redispatch request, never both.""",
    "outputFields": """\
Each recipe must include:
  - title              : short imperative (4-9 words, e.g. "Add TypeORM migration with rollback").
  - intent             : single-sentence pattern label, "what kind of work is this?" (under 200 chars).
  - description        : the ONLY signal a future agent sees when deciding whether to pull this recipe
                         (the menu it reads shows title and description alone, nothing else). Write it
                         SKILL.md-style: when this recipe applies plus what it does, specific enough
                         to trigger on a matching task and skip on a non-matching one. Under 400 chars.
  - summary_md         : Markdown body, 4-15 lines. Describe the workflow at a high level. Use bullet
                         points. Reference identifiers/files/tools verbatim.
  - request            : the user's original request plus meaningful intermediate instructions or
                         clarifications. Summarize, do not invent.
  - corrections        : list of {whatAgentDid, howCorrected, evidence}. evidence MUST contain at
                         least one real eventId a specialist surfaced; a correction whose evidence
                         cannot be verified rejects the candidate.
  - pitfalls           : list of {pitfall, whyNonObvious, evidence}. Same evidence requirement as
                         corrections.
  - governing_rules    : list of rule IDs the specialists reported that already govern important parts
                         of this workflow or its friction.
  - revises_recipe_id  : optional existing recipe ID a specialist reported, when this candidate should
                         update that recipe.
  - steps              : optional ordered list of high-level actions (1-10 entries). Each step:
                         {order, action, rationale?, verify?}. order MUST start at 1 and run
                         consecutively with no gaps (1, 2, 3, ...).
                         verify is an optional observable signal a future run of this recipe can be
                         checked against: {kind: "command", commandMatches: [...]} (1-20 strings,
                         matched as substrings of a command actually run), {kind: "pattern",
                         pattern: "..."} (a regex against paths or commands touched, under 500
                         chars), or {kind: "action", tool: "command"|"file-read"|"file-write"|"web"}
                         (any call of that tool family). Fill verify ONLY when the excerpts already
                         show the trajectory's own tool calls carrying the step out; a
                         plausible-sounding but unobserved verify is worse than none, so leave it out
                         when you are not certain.
  - touched_files      : optional list of file paths or path patterns this recipe commonly touches.
                         Each: {path, role: "read"|"write"|"both"}.
  - contributing_slices: REQUIRED. Include the anchor task and any inspected similar tasks that
                         contributed evidence. Each entry: {taskId, turnIds, eventIds}. Cite actual
                         IDs only. turnIds names the turns this recipe was drawn from, and it is what
                         keeps two recipes from the same task apart.
  - rationale          : one sentence (under 500 chars) explaining why this task produced a useful
                         recipe.""",
    "qualityRules": """\
  - Quality over quantity. Empty output is acceptable if no meaningful pattern emerges.
  - Return at most ${recipeCandidateLimit} candidates, and never two candidates for the same turns.
  - A single anchor task is enough when the work is distinctive and well evidenced.
  - Do NOT invent revises_recipe_id. Only use an ID a specialist reported; any other value rejects the
    candidate. Setting it never overwrites the existing recipe directly: the server always queues your
    output as a new candidate linked to that recipe, and a human must approve it before it replaces
    anything.
  - Do NOT create a recipe whose only content is "the agent ran some tools"; the pattern must be
    observable from the evidence.
  - Identifiers (file paths, tool names, commands) MUST be preserved verbatim even when prose is
    translated.
  - The model may suggest recipe content only. It has no authority to overwrite an existing recipe.""",
    "repairDirective": """\
Change only what is necessary to satisfy these errors, using only identifiers the specialists
surfaced. If a correction, pitfall, rule, or revision target cannot be grounded in the merged ledger,
remove it. Confirm with ${checkCitationsTool} before you resubmit. Then return the complete repaired
candidate list.""",
    "specialistCatalog": """\
Three specialists can be dispatched, each reading in its own isolated context:

- timeline: reads the anchor task's own events end to end, paging or searching within it for what
  matters.
- rules: reads the rules that already govern the anchor and the recipes that already exist.
- repetition: searches other tasks for the same workflow to judge whether it recurs.""",
    "dispatchWeighting": """\
Assign only the specialists this anchor actually needs, give each a concrete question, and give each a
weight from 1 to ${maxProbeWeight} reflecting how much of the investigation its question deserves. Put
weight where the evidence is; a specialist you do not need is a specialist you should not dispatch.
Weights are relative to each other, not a count you can run out of or ask more of than exists, and the
budget is split between specialists in proportion to them.""",
    "emptyPlan": """\
Only the specialists read the ledger, so an empty probes list means there is nothing here to
investigate and the run ends with no recipes. Return it only when you mean exactly that; dispatch at
least one specialist whenever this anchor is worth reading at all.""",
    "specialistCharter": """\
You investigate the one question the coordinator gave you, using only the tools you hold, and report
back. You do not write recipes; the coordinator does that from your report and the other
specialists'.""",
    "specialistReporting": """\
Report a verdict that answers your question directly, and attach the excerpts the coordinator needs
to write from — quote the evidence rather than summarising it away, because the coordinator cannot
see what you read. Every excerpt must name the event it came from. Verify with ${checkCitationsTool}
before you report: an ID the coordinator cannot cite is worse than no evidence at all.""",
}

RECIPE_SCAN_FRAGMENT_REGISTRY = build_fragment_registry(
    agent="recipe-scan",
    language="en",
    contents=FRAGMENTS,
    usages={
        "recipeDefinition": ("recipe-scan.investigator.system",),
        "evidenceSourcing": ("recipe-scan.investigator.system",),
        "citationDiscipline": ("recipe-scan.investigator.system",),
        "turnSplitting": ("recipe-scan.investigator.system",),
        "candidateBudget": ("recipe-scan.investigator.system",),
        "redispatchProtocol": ("recipe-scan.investigator.system",),
        "outputFields": ("recipe-scan.investigator.system",),
        "qualityRules": ("recipe-scan.investigator.system",),
        "repairDirective": ("recipe-scan.investigator.repair",),
        "specialistCatalog": ("recipe-scan.survey.system",),
        "dispatchWeighting": ("recipe-scan.survey.system",),
        "emptyPlan": ("recipe-scan.survey.system",),
        "specialistCharter": ("recipe-scan.probe.system",),
        "specialistReporting": ("recipe-scan.probe.system",),
    },
)
