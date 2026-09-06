.PHONY: test qa clean install studio cli gui

test:
	python tests/test_suite.py

qa:
	python tests/qa_benchmark.py

studio:
	python main.py --web

cli:
	python main.py --cli

gui:
	python main.py --gui

clean:
	rm -rf __pycache__ *.pyc recovered_files/ *.map

install:
	pip install -e .
