/* v2 response boundary, validated against the canonical fixture in CI. */
(function (root) {
  function validateTurn(value) {
    const fields = ["conversation_id", "turn_id", "spoken_answer", "action_id", "confirmation_summary",
      "provider", "model", "prompt_version"];
    if (!value || value.contract_version !== "2" || fields.some(k => typeof value[k] !== "string") ||
        !["answer", "allow", "require_confirmation", "deny"].includes(value.decision)) {
      throw new Error("Unsupported runtime response");
    }
    return value;
  }
  if (typeof module !== "undefined") module.exports = { validateTurn };
  else root.JarvisContract = { validateTurn };
})(globalThis);
