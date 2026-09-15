# Contributing to TeleForge

<p align="center">
  <img src="https://img.shields.io/badge/PRs-Welcome-brightgreen?style=flat-square" alt="PRs Welcome">
  <img src="https://img.shields.io/badge/Commits-Conventional%20Commits-FE5196?style=flat-square" alt="Conventional Commits">
  <img src="https://img.shields.io/badge/Code%20Style-Minimalist-blue?style=flat-square" alt="Minimalist Code Style">
</p>

Guidelines for contributing improvements, fixes, and new features to TeleForge.

## Development Workflow

1. **Fork and Clone**:
   ```bash
   git clone https://github.com/itswill00/TeleForge.git
   cd TeleForge
   ./setup.sh
   ```

2. **Branching**:
   Create a focused feature or fix branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

3. **Read the Developer Guide**:
   Consult [DEVELOPMENT.md](DEVELOPMENT.md) for architectural guidelines, `@on_cmd` decorator syntax, LocalDB state management, and error handling.

4. **Testing & Diagnostics**:
   Ensure all Python modules compile cleanly and pass diagnostic self-checks:
   ```bash
   ./setup.sh
   ```

5. **Commit Standards**:
   All commits must follow Conventional Commits format with developer sign-off (`git commit -s`):
   ```bash
   git commit -s -m "feat(module): add new utility command" -m "Detailed explanation of changes."
   ```

6. **Pull Request**:
   Push your branch and open a PR against `main`. Ensure descriptions are clear, technical, and concise.

## Code Standards

- **Zero Bloat**: Avoid unnecessary external dependencies. Prioritize Python stdlib and existing Pyrogram utilities.
- **Resilience**: Every command handler must handle potential Telegram exceptions (`FloodWait`, `MessageNotModified`).
- **Clean Formatting**: No decorative ASCII divider lines are permitted in code comments or documentation.
