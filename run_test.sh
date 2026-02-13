#!/bin/bash

for d in tests/*; do
	./tree2.py -d "$d"
	if ! diff g.dot "$d/g.dot"; then
		echo "Differences found for $d"
		exit 1
	fi
done

echo "All tests passed"
