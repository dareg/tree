#!/usr/bin/env python3

import argparse
import xml.etree.ElementTree as ET
import sys
import pyfxtran
from pathlib import Path


class Node:
    def __init__(self, name, filename=None):
        self.name = name
        self.callees = set()
        self.filename = [filename]

    def add_callee(self, callee):
        self.callees.add(callee)

    def add_filename(self, filename):
        self.filename.append(filename)

    def is_calling(self, callee):
        return callee in self.callees


nodes = {}

ns = "{http://fxtran.net/#syntax}"


def simplify_xml(lines):
    # remove namespace, add node containing subroutine
    lines2 = []
    for line in lines:
        if "<sub><subroutine-stmt>" in line:
            continue
        if "</end-subroutine-stmt></sub>" in line:
            continue
        line = line.replace('xmlns="http://fxtran.net/#syntax"', "")
        #        line=line.replace("<subroutine-stmt>","<sub><subroutine-stmt>")
        #        line=line.replace("</end-subroutine-stmt>","</end-subroutine-stmt></sub>")
        lines2.append(line)
    return "".join(lines2)


# def get_procs(xml_file):
#    root = ET.fromstring(xml_file)
#    procs=[]
#    for elem in root.iter():
#        if elem.tag == "sub":
#            procs.append(elem)
#
#    return procs


def get_procs(xml_file):
    root = ET.fromstring(xml_file)
    procs = root.findall(".//program-unit[subroutine-stmt]")
    procs.extend(root.findall(".//program-unit[function-stmt]"))
    return procs


def remove_contained(proc):
    for elt in proc.findall(".//program-unit"):
        proc.remove(elt)
    return proc


def analyze_file(filename):
    # print("Working on ", filename)
    file = pyfxtran.run(filename, ["-construct-tag", "-o", "-"])

    src = file.replace('xmlns="http://fxtran.net/#syntax"', "")
    # src=simplify_xml(file)
    procs = get_procs(src)

    for proc in procs:
        # proc=remove_contained(proc)
        # print(ET.tostring(proc))
        proc_name = ""

        sub = proc.find(f"./subroutine-stmt/subroutine-N/N/n")
        if sub is not None:
            proc_name = sub.text

        func = proc.find(f"./function-stmt/function-N/N/n")
        if func is not None:
            proc_name = func.text

        if not proc_name:
            continue

        node = None
        if proc_name in nodes:
            node = nodes[proc_name]
            node.add_filename(filename)
        else:
            node = Node(proc_name, filename)

        calls = proc.findall(".//call-stmt")
        for call in calls:
            callee = call.find("procedure-designator/named-E/N/n").text
            node.add_callee(callee)

        nodes[proc_name] = node


def generate_dotfile():
    g = "digraph G{\n\tnode [shape=box, style=filled];\n"
    for node in nodes:
        g = g + f'{node}[label="{node}"];\n'
        for callee in nodes[node].callees:
            g = g + f"{node} -> {callee};\n"
    g = g + "}\n"
    print(g)


def work_on_dir(dirname):
    root = Path(dirname)
    for filename in root.glob("**/*.F90"):
        analyze_file(filename)


def work_on_pack(dirname):
    root = Path(dirname)
    main = set()
    local = set()
    for filename in root.glob("src/main/**/*.F90"):
        main.add(str(filename).replace(str(root) + "/src/main/", ""))
    for filename in root.glob("src/local/**/*.F90"):
        local.add(str(filename).replace(str(root) + "/src/local/", ""))
    print(main)
    print(local)
    print(main.intersection(local))
    print(len(main), len(main - main.intersection(local)))

    # all_infos.append(analyze_file(filename))
    # generate_dotfile(all_infos)


parser = argparse.ArgumentParser(prog="tree")
parser.add_argument("-p", "--pack")
parser.add_argument("-d", "--directory")
parser.add_argument("-f", "--from")
args = parser.parse_args()

if args.directory:
    work_on_dir(args.directory)
    generate_dotfile()
elif args.pack:
    work_on_pack(args.pack)
