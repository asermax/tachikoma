# Feature Designs

Implementation approaches organized by domain. Each design corresponds to a feature spec.

## Capability Domains

| Domain | Description |
|--------|-------------|
| [agent](agent/) | Core agent loop, SDK integration, and message processing |
| [channels](channels/) | Communication interfaces for interacting with the agent |
| [configuration](configuration/) | Application configuration management |
| [delivery](delivery/) | Buffered delivery of background-originated items to the user |
| [distribution](distribution/) | Package building, versioning, and publishing infrastructure |
| [detached-processes](detached-processes/) | Supervision of detached OS shell commands that outlive Tachikoma |
| [memory](memory/) | Automatic memory extraction from conversations |
| [plugins](plugins/) | Third-party plugin install, discovery, and loading |
| [tasks](tasks/) | Proactive task scheduling and execution |
| [workflows](workflows/) | Multi-step workflow state machine within skills |
