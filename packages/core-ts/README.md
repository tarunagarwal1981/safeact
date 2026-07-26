# safeact (TypeScript)

**The safety checkpoint every AI action passes through — before it happens.**

This package reserves the `safeact` name on npm. The TypeScript SDK is in progress.

- Website: https://safeact.ai
- Python core (available now): https://pypi.org/project/safeact

The SDK will be a thin client over the SafeAct control plane: check an action against your
rules, hold it for human approval when confidence is low, guarantee it runs exactly once, and
keep a tamper-evident receipt.

```ts
// coming soon
import { safe } from "safeact";
```
