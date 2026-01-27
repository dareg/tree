#!/usr/bin/env python3

# Judicaël Grasset - Metéo-France 2025-2026

import sqlite3
import argparse
import textwrap
import xml.etree.ElementTree as ET
import sys
import pyfxtran
from pathlib import Path
import subprocess
from collections import Counter

verbose = False


class Node:
    def __init__(self, name, filename=None):
        self.name = name
        self.drhook = False
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


def get_procs(xml_file):
    root = ET.fromstring(xml_file)

    # Remove nodes containing the interfaces (usually modi_* files)
    for parent in root.iter():
        children_to_remove = parent.findall("interface-construct")
        for child in children_to_remove:
            parent.remove(child)

    procs = root.findall(".//program-unit[subroutine-stmt]")
    procs.extend(root.findall(".//program-unit[function-stmt]"))
    procs.extend(root.findall(".//program-unit[program-stmt]"))
    return procs


def remove_contained(proc):
    for elt in proc.findall(".//program-unit"):
        proc.remove(elt)
    return proc


def fxtran_process_file(filename):
    file = None
    try:
        file = pyfxtran.run(
            filename,
            ["-construct-tag", "-no-include", "-line-length", "9999", "-o", "-"],
        )
    except subprocess.CalledProcessError:
        print(
            f"Error when processing {filename} with fxtran, this file will be ignored"
        )
    return file


def analyze_file(filename, nodes, to_excludes):
    if verbose:
        print("Working on ", filename)
        print("[as verbose")

    file = fxtran_process_file(filename)
    src = file.replace('xmlns="http://fxtran.net/#syntax"', "")
    procs = get_procs(src)

    for proc in procs:
        proc_name = ""

        sub = proc.find(f"./subroutine-stmt/subroutine-N/N/n")
        if sub is not None:
            proc_name = sub.text

        func = proc.find(f"./function-stmt/function-N/N/n")
        if func is not None:
            proc_name = func.text

        prog = proc.find(f"./program-stmt/program-N/N/n")
        if prog is not None:
            proc_name = prog.text

        if not proc_name:
            continue
        proc_name = proc_name.upper()

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
            callee = callee.upper()
            if callee in to_excludes:
                continue

            node.add_callee(callee)

        nodes[proc_name] = node


def generate_dotfile(nodes, dotfile):
    g = "digraph G{\n\tnode [shape=box, style=filled];\n"
    for node in nodes:
        node_color = ""
        label = node
        for filename in nodes[node].filename:
            label += "\\n" + filename.name

        if nodes[node].drhook:
            node_color = ', style=filled, fillcolor="#f7c93d"'

        g = g + f'{node}[label="{label}"{node_color}];\n'
        for callee in nodes[node].callees:
            g = g + f"{node} -> {callee};\n"
    g = g + "}\n"
    fh = open(dotfile, "w")
    fh.write(g)


def generate_sqlite(nodes):
    conn = sqlite3.connect("g.db")
    cursor = conn.cursor()

    cursor.execute("""DROP TABLE IF EXISTS Proc;""")
    cursor.execute("""DROP TABLE IF EXISTS Call;""")
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS Proc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        drhook INTEGER NOT NULL,
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
        cursor.execute(
            """INSERT INTO Proc (name, drhook) VALUES (?,?)""",
            (node, nodes[node].drhook),
        )
    for node in nodes:
        for callee in nodes[node].callees:
            cursor.execute(
                """INSERT INTO Proc (name, drhook) VALUES (?,?) ON CONFLICT DO NOTHING""",
                (callee, 0),
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
    root = root.upper()
    if root not in nodes:
        return nodes2
    to_study = [root]

    while len(to_study) > 0:
        if to_study[0] in nodes and to_study[0] not in nodes2:
            nodes2[to_study[0]] = nodes[to_study[0]]
            for callee in nodes[to_study[0]].callees:
                to_study.append(callee)
        to_study.pop(0)
    return nodes2


def read_excludes_list(filename):
    to_excludes = set()
    with open(filename, "r") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
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


def stats(nodes, drhook_path):
    called = {}
    for node in nodes:
        for callee in nodes[node].callees:
            if callee in called:
                called[callee] += 1
            else:
                called[callee] = 0
    most_common = Counter(called).most_common()

    for i in range(min(3, len(most_common))):
        print(f"{most_common[i][0]} is called {most_common[i][1]} times")

    if drhook_path:
        drhook = 0
        for node in nodes:
            if nodes[node].drhook:
                drhook += 1
        print(drhook, "subroutines has been seen in the drhook.txt file")

        only_drhook = 0
        for sub in read_drhook(drhook_path):
            if sub not in nodes:
                only_drhook += 1
        print(
            only_drhook,
            "subroutines were in drhook.txt but not in analyzed source files",
        )
        print("Total number of routines in drhook:", drhook + only_drhook)


def read_drhook(drhook_prof_dir):
    called = set()
    all_drhook_prof = Path(drhook_prof_dir).glob("drhook.prof.*")
    for drhook_prof in all_drhook_prof:
        fh = open(drhook_prof, "r")
        for line in fh.readlines()[16:]:
            line = str(line.split()[-1])
            line = line.split("@")[0]
            line = line.replace("*", "")
            line = line.upper()
            called.add(line)
        if verbose:
            print(f"Read {drhook_prof}, {len(called)} subroutines called")
    return called


def mark_as_seen_in_drhook(nodes, called):
    for node in nodes:
        if nodes[node].name in called:
            nodes[node].drhook = True


def remove_if_not_in_drhook_and_callees(nodes, called):
    nodes2 = {}
    for node in nodes:
        if node in called:
            nodes[node].drhook = True
            nodes2[node] = nodes[node]
    return nodes2


def remove_if_not_in_drhook(nodes, called):
    nodes2 = {}
    for node in nodes:
        if node in called:
            nodes[node].drhook = True
            nodes2[node] = nodes[node]
    for node in nodes2:
        callees = set()
        for call in nodes2[node].callees:
            if call in nodes2:
                callees.add(call)
        nodes2[node].callees = callees
    return nodes2


def nounused(nodes):
    # Remove from the list of nodes, all the nodes that are neither called neither calling something
    all_callees = set()
    for node in nodes:
        all_callees.update(nodes[node].callees)

    nodes2 = {}
    for node in nodes:
        to_add = False
        # If it'a called
        if node in all_callees:
            to_add = True
        # If it's calling something
        if nodes[node].callees:
            to_add = True

        if to_add:
            nodes2[node] = nodes[node]

    return nodes2


def handle_cli_options():
    parser = argparse.ArgumentParser(
        prog="tree",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
Generate a call tree in a dot file from a pack or a directory containing
Fortran code. Once the dot file is generated, one can use the dot command
to generate an image, a pdf and so on:
    dot -Tpdf -o g.pdf g.dot

The drhook options take a directory in argument. This directory must contains
all the dr_hook.prof.* files generated during the run. To generate those files
the environment variable DR_HOOK must be set to 1 and DR_HOOK_OPT must be set
to prof.
    export DR_HOOK=1
    export DR_HOOK_OPT=prof
        """
        ),
    )
    parser.add_argument("-p", "--pack", metavar="PACK_DIRECTORY")
    parser.add_argument("-d", "--directory")
    parser.add_argument(
        "-f",
        "--cutfrom",
        help="Only shows subroutines called from this subroutine or one of its callee",
        metavar="SUBROUTINE_NAME",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--dot", help="Name of the generated dotfile", metavar="FILE", default="g.dot"
    )
    parser.add_argument("--db", action="store_true")
    parser.add_argument(
        "-s",
        "--stats",
        action="store_true",
        help="Show some statistics after the execution",
    )
    parser.add_argument(
        "-e",
        "--excludes",
        help="File containing on each line a subroutine's name to exclude from the graph",
        metavar="FILE",
    )
    parser.add_argument(
        "-k",
        "--known",
        help="Only show the calls to subroutines that are known from the parsed code",
        action="store_true",
    )
    parser.add_argument(
        "--nounused",
        help="Remove the procedures that are neither called nor calling something",
        action="store_true",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--drhook",
        help="Highlights the subroutines that are also in the drhook_prof.* files",
        metavar="DIRECTORY",
    )
    group.add_argument(
        "--drhookonly",
        help="Show only the subroutines that are in the parsed codebase and in the drhook_prof.* files",
        metavar="DIRECTORY",
    )
    group.add_argument(
        "--drhookcallees",
        help="Show the subroutines that are in the parsed codebase and in the drhook_prof.* files and the callees of those subroutines",
        metavar="DIRECTORY",
    )
    args = parser.parse_args()
    return args


def main():
    args = handle_cli_options()
    nodes = {}
    to_excludes = set()

    if args.verbose:
        global verbose
        verbose = True

    if args.excludes:
        to_excludes = read_excludes_list(args.excludes)

    if args.directory:
        nodes = work_on_dir(args.directory, to_excludes)
    elif args.pack:
        nodes = work_on_pack(args.pack, to_excludes)

    if args.known:
        nodes = keep_known(nodes)

    if args.nounused:
        nodes = nounused(nodes)

    if args.cutfrom:
        nodes = cut_before(args.cutfrom, nodes)
        if not nodes:
            print(
                f"Couldn't find subroutine {args.cutfrom} in the analyzed files",
                file=sys.stderr,
            )
            return

    drhook_path = ""
    if args.drhook:
        drhook_path = args.drhook
        called = read_drhook(drhook_path)
        mark_as_seen_in_drhook(nodes, called)

    if args.drhookcallees:
        drhook_path = args.drhookcallees
        called = read_drhook(drhook_path)
        nodes = remove_if_not_in_drhook_and_callees(nodes, called)

    if args.drhookonly:
        drhook_path = args.drhookonly
        called = read_drhook(drhook_path)
        nodes = remove_if_not_in_drhook(nodes, called)

    if args.dot:
        generate_dotfile(nodes, args.dot)
    if args.db:
        generate_sqlite(nodes)

    if args.stats:
        stats(nodes, drhook_path)


main()
