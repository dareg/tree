#!/usr/bin/env python3

# Judicaël Grasset - Metéo-France 2025-2026

import sqlite3
import argparse
import xml.etree.ElementTree as ET
import sys
import pyfxtran
from pathlib import Path
import subprocess


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


def analyze_file(filename, nodes, to_excludes):
    print("Working on ", filename)
    try:
        file = pyfxtran.run(
            filename,
            ["-construct-tag", "-no-include", "-line-length", "9999", "-o", "-"],
        )
    except subprocess.CalledProcessError:
        print(
            f"Error when processing {filename} with fxtran, this file will be ignored"
        )
        return

    src = file.replace('xmlns="http://fxtran.net/#syntax"', "")
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

        if proc_name in to_excludes:
            continue

        node = None
        if proc_name in nodes:
            node = nodes[proc_name]
            node.add_filename(filename)
        else:
            node = Node(proc_name, filename)

        calls = proc.findall(".//call-stmt")
        for call in calls:
            # the 'cpp' node might be added by a macro between 'N' and 'n' (see call abor1 in bator_pool_balance_mod.F90)
            callee = call.find(".//procedure-designator/named-E/N//n").text
            if callee in to_excludes:
                continue

            node.add_callee(callee)

        nodes[proc_name] = node


def generate_dotfile(nodes):
    g = "digraph G{\n\tnode [shape=box, style=filled];\n"
    for node in nodes:
        label = node
        for filename in nodes[node].filename:
            label += "\\n" + filename.name
        g = g + f'{node}[label="{label}"];\n'
        for callee in nodes[node].callees:
            g = g + f"{node} -> {callee};\n"
    g = g + "}\n"
    fh = open("g.dot", "w")
    fh.write(g)


def generate_sqlite(nodes):
    conn = sqlite3.connect("g.db")
    cursor = conn.cursor()

    cursor.execute("""DROP TABLE IF EXISTS Proc;    """)
    cursor.execute("""DROP TABLE IF EXISTS Call;    """)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS Proc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        CONSTRAINT unq UNIQUE(name)
    )
    """
    )
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS Call (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        caller INTEGER,
        callee INTEGER,
        FOREIGN KEY (caller) REFERENCES Proc(id),
        FOREIGN KEY (callee) REFERENCES Proc(id)
    )
    """
    )
    conn.commit()

    # insert all procedures
    for node in nodes:
        label = node
        cursor.execute("""INSERT INTO Proc (name) VALUES (?)""", (node,))
    for node in nodes:
        for callee in nodes[node].callees:
            cursor.execute(
                """INSERT INTO Proc (name) VALUES (?) ON CONFLICT DO NOTHING""",
                (callee,),
            )

    # add all the calls
    for node in nodes:
        for callee in nodes[node].callees:
            cursor.execute(
                """SELECT id FROM Proc WHERE name = ? ORDER BY id DESC LIMIT 1""",
                (node,),
            )
            caller_id = cursor.fetchone()[0]
            cursor.execute(
                """SELECT id FROM Proc WHERE name = ? ORDER BY id DESC LIMIT 1""",
                (callee,),
            )
            res = cursor.fetchone()
            if not res:
                continue
            callee_id = res[0]
            print(f"{node}({caller_id}) -> {callee}({callee_id})")

            cursor.execute(
                """INSERT INTO Call (caller,callee) VALUES (?,?)""",
                (
                    caller_id,
                    callee_id,
                ),
            )

    conn.commit()
    conn.close()


def work_on_dir(dirname, to_excludes):
    root = Path(dirname)
    nodes = {}
    for filename in root.glob("**/*.F90"):
        analyze_file(filename, nodes, to_excludes)

    return nodes


def work_on_pack(dirname, to_excludes):
    root = Path(dirname)
    main = set()
    local = set()
    nodes = {}
    for filename in root.glob("src/main/**/*.F90"):
        main.add(str(filename).replace(str(root) + "/src/main/", ""))
    for filename in root.glob("src/local/**/*.F90"):
        local.add(str(filename).replace(str(root) + "/src/local/", ""))

    filenames = []
    for filename in main:
        if filename not in local:
            filenames.append(Path(root) / Path("src/main/") / Path(filename))
    for filename in local:
        filenames.append(Path(root) / Path("src/local/") / Path(filename))

    for filename in filenames:
        analyze_file(filename, nodes, to_excludes)

    return nodes


def cut_before(root, nodes):
    nodes2 = {}
    if root not in nodes:
        return
    nodes2[root] = nodes[root]
    to_study = [root]
    while len(to_study) > 0:
        if to_study[0] in nodes:
            nodes2[to_study[0]] = nodes[to_study[0]]
            for callee in nodes[to_study[0]].callees:
                to_study.append(callee)
        to_study.pop(0)
    return nodes2


def read_excludes_list(filename):
    to_excludes = set()
    with open(filename, "r") as fh:
        for line in fh:
            if line:
                to_excludes.add(line.strip().upper())
    return to_excludes


def keep_known(nodes):
    for node in nodes:
        callees2 = set()
        for callee in nodes[node].callees:
            if callee in nodes:
                callees2.add(callee)
        nodes[node].callees = callees2
    return nodes


def main():
    parser = argparse.ArgumentParser(prog="tree")
    parser.add_argument("-p", "--pack")
    parser.add_argument("-d", "--directory")
    parser.add_argument(
        "-f",
        "--cutfrom",
        help="Only shows subroutines called from this subroutine or one of its callee",
    )
    parser.add_argument("--dot", action="store_true")
    parser.add_argument("--db", action="store_true")
    parser.add_argument(
        "-e",
        "--excludes",
        help="File containing on each line a subroutine name to exclude from the graph",
    )
    parser.add_argument(
        "-k",
        "--known",
        help="Only show the calls to subroutines that are known",
        action="store_true",
    )
    args = parser.parse_args()

    nodes = {}
    to_excludes = set()
    if args.excludes:
        to_excludes = read_excludes_list(args.excludes)

    if args.directory:
        nodes = work_on_dir(args.directory, to_excludes)
    elif args.pack:
        nodes = work_on_pack(args.pack, to_excludes)

    if args.known:
        nodes = keep_known(nodes)

    if args.cutfrom:
        nodes = cut_before(args.cutfrom, nodes)

    if args.dot:
        generate_dotfile(nodes)
    if args.db:
        generate_sqlite(nodes)


main()
