# boss-agent-cli HyperFrames Showcase

This directory contains a HyperFrames source composition for a short project showcase animation.

## Preview

```bash
npx hyperframes preview demo/hyperframes-showcase --port 3017
```

Open the Studio URL reported by the CLI, usually:

```text
http://localhost:3017/#project/hyperframes-showcase
```

## Validate

```bash
npx hyperframes lint demo/hyperframes-showcase
npx hyperframes inspect demo/hyperframes-showcase --samples 15
```

## Render

```bash
npx hyperframes render demo/hyperframes-showcase --output boss-agent-cli-showcase.mp4 --quality standard
```

The composition is 16 seconds at 1920x1080 and is designed around five beats:

1. Project identity
2. Agent capability discovery
3. Welfare-aware job search
4. JSON envelope contract
5. Open-source quality signal
