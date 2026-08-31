# Tachikoma (pi/TypeScript) task runner

default:
    @just --list

install:
    pnpm install

run *args:
    node src/main.ts {{ args }}

test *args:
    pnpm vitest run {{ args }}

coverage *args:
    pnpm vitest run --coverage {{ args }}

lint:
    pnpm biome check .

fmt:
    pnpm biome check --write .

typecheck:
    pnpm tsc --noEmit

build:
    pnpm run build

check: lint typecheck test

release *args:
    pnpm semantic-release {{ args }}
