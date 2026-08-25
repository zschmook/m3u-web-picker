# Commercial Detection Design Notes

## Channel-balanced global cold-start seed

### Goal

Keep every channel's commercial profile independent while allowing an unfamiliar
channel to start with a weak hint derived from other mature, compatible channels.
The global seed must never copy another channel's logo fingerprint, position, or
raw observations into the new channel's history.

### Contributor eligibility

A channel may contribute to a global seed after it has approximately:

- three hours of retained observations;
- 20–30 minutes of commercial observations;
- at least six complete commercial-to-program transitions; and
- a usable balance of program and commercial samples.

Elapsed time alone is not sufficient. Conventional bug-based television,
sports-generated channels, and bugless/FAST-style channels should use separate
global pools.

### Channel-balanced global score

For current feature window `x`, each compatible mature channel supplies its own
commercial score `S_c(x)`. The target channel is excluded from its seed, and each
contributing channel receives approximately equal influence regardless of how
many hours it was watched.

Convert bounded scores to log-odds:

```text
z_c = log(S_c / (1 - S_c))
```

Then calculate a robust aggregate and convert it back to a percentage:

```text
S_global = sigmoid(trimmed_mean(z_1, z_2, ... z_k))
```

The trimmed mean prevents one unusual channel from pulling the global seed too
far in either direction. A channel's quality can slightly reduce its vote, but
additional hours beyond maturity must not increase its voting power.

### Local maturity

Let:

- `H_p` be retained hours of program observations;
- `H_a` be retained hours of commercial observations; and
- `T` be complete observed commercial transitions.

A proposed local maturity value is:

```text
m = cube_root(
    (1 - exp(-H_p / 0.75))
  * (1 - exp(-H_a / 0.25))
  * (1 - exp(-T / 6))
)
```

This makes maturity depend on program footage, commercial footage, and actual
boundaries. Approximate expected values are:

- new channel: `m = 0`;
- one representative broadcast hour: `m ~= 0.55`;
- three useful hours: `m ~= 0.90`; and
- established channel: `m` approaches `1.0`.

### Effective score

Blend the global seed with the independent local score:

```text
S_effective = (1 - m) * S_global + m * S_channel
```

Example: if the global broadcast seed scores a frame at 82% commercial and a
one-hour CBS profile scores it at 65%, with `m = 0.55`:

```text
S_effective = 0.45 * 82 + 0.55 * 65 = 72.7%
```

If the same channel later reaches `m = 0.90`:

```text
S_effective = 0.10 * 82 + 0.90 * 65 = 66.7%
```

The inherited hint therefore fades naturally as local evidence accumulates.

### Feedback safeguards

- Never write global predictions into local history as confirmed facts.
- Do not allow a seeded channel to contribute back to the global pool until it
  independently satisfies maturity and transition-quality requirements.
- Weight manual corrections and confirmed short false positives more strongly
  than uncorrected inferred labels.
- Balance program and commercial evidence inside each channel before creating
  its contribution.
- Cap or compress repetitive samples so always-on NBC-family channels cannot
  overwhelm sparsely watched CBS or ABC stations.
- Validate changes by withholding complete channels and measuring whether the
  global seed improves those unseen channels.

### Diagnostics

The UI should eventually distinguish:

- effective commercial score;
- independent local-channel score;
- global seed score;
- local maturity percentage; and
- number and type of mature contributing channels.

No global seed should be used until enough compatible mature channels exist to
produce a stable aggregate.
