
# A4 Adversarial Robotics Benchmark — Makefile


SHELL := /bin/bash

.PHONY: help up down test smoke ablation logs clean lint

help:
	@echo ""
	@echo "  A4 Benchmark Commands"
	@echo "  ════════════════════════════════════════════════════"
	@echo "  make up          Start Docker containers"
	@echo "  make down        Stop Docker containers"
	@echo "  make test        Run pre-ablation setup checks (12 tests)"
	@echo "  make smoke       Quick smoke test (first 5 prompts only)"
	@echo "  make ablation    Launch full 9-model ablation (background)"
	@echo "  make logs        Tail the live ablation log"
	@echo "  make clean       Remove old run results"
	@echo "  make lint        Run ruff linter on Python sources"
	@echo ""

up:
	@echo "Starting Docker environment..."
	docker compose up -d
	@echo "Ready."

down:
	@echo "Stopping Docker environment..."
	docker compose down

test:
	@bash scripts/test_ablation_setup.sh

smoke:
	@bash scripts/run_full_ablation.sh --smoke --no-llm-critic

ablation:
	@echo "Starting full ablation in background..."
	nohup bash scripts/run_full_ablation.sh --no-llm-critic > data/results/ablation_master.log 2>&1 &
	@echo "Started. Run 'make logs' to follow progress."

logs:
	@tail -f data/results/ablation_master.log

clean:
	@echo "Cleaning old run results..."
	rm -rf data/results/runs/*
	rm -f data/results/ablation_master.log
	@echo "Done."

lint:
	@echo "Running ruff..."
	ruff check scripts/ src/ --fix
	@echo "Done."
