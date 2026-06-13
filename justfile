# Tachikoma (pi/TypeScript) task runner

default:
    @just --list

install:
    pnpm install

run *args:
    node src/main.ts {{ args }}

test *args:
    pnpm vitest run {{ args }}

lint:
    pnpm biome check .

fmt:
    pnpm biome check --write .

typecheck:
    pnpm tsc --noEmit

build:
    rm -rf dist && pnpm tsc -p tsconfig.build.json

check: lint typecheck test

release *args:
    pnpm commit-and-tag-version {{ args }}
