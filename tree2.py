#!/usr/bin/env python3

# Judicaël Grasset - Metéo-France 2025-2026

from collections import Counter
from pathlib import Path
import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET

verbose = False


class Procedure:
    def __init__(self, name, alias):
        self.name = name
        self.alias = alias

    def __str__(self):
        return f"{self.alias} => {self.name}"


class DerivedType:
    def __init__(self, name, filename):
        self.name = name
        self.filename = filename
        self.procedures = []
        self.members = {}

    def add_member(self, name, typename):
        self.members[typename] = name

    def add_procedure(self, name, alias):
        self.procedures.append(Procedure(name, alias))

    def __str__(self):
        return f"{self.filename}\nType:{self.name}\n\tProc:{', '.join([str(x) for x in self.procedures])}\n\tSubtype:{self.members}"


derived_types = {}


class MethodCall:
    def __init__(self, t):
        self.type = t
        self.cts = []


class Node:
    def __init__(self, name, filename=None):
        self.name = name
        self.drhook = False
        self.callees = set()
        self.method_callees = []
        self.filename = [filename]
        self.hide = False

    def add_callee(self, callee):
        self.callees.add(callee)

    def add_filename(self, filename):
        self.filename.append(filename)

    def is_calling(self, callee):
        return callee in self.callees

    def __str__(self):
        s = f"{self.name}"
        for filename in self.filename:
            s += f" {filename}"
        s += f"\n\tDR_HOOK:{self.drhook}, hide:{self.hide}\n"
        if self.callees:
            s += "\t"
        s += ", ".join([callee for callee in self.callees])
        return s


def get_derived_type_var(proc):
    variables = {}
    decls = proc.findall("./T-decl-stmt")
    for decl in decls:
        derived_type = decl.find(".//derived-T-spec/T-N/N/n")
        if derived_type is None:
            continue
        derived_type = derived_type.text.upper()

        for varname in decl.findall(".//EN-decl/EN-N/N/n"):
            varname = varname.text.upper()
            variables[varname] = derived_type
    return variables


def get_derived_type_procedures(nodes, filename):
    dt_nodes = nodes.findall(".//T-construct")
    for dt_node in dt_nodes:
        if dt_node.find(".//contains-stmt") is None:
            continue
        typename = dt_node.find(".//T-stmt/T-N/N/n").text.upper()
        dt = DerivedType(typename, filename)

        contained_procs = dt_node.findall(".//procedure-stmt/")
        for contained_proc in contained_procs:
            alias = contained_proc.find("./rename/use-N/n").text.upper()
            name = contained_proc.find("./rename/N/n").text.upper()
            dt.add_procedure(name, alias)

        derived_types[typename] = dt


def print_nodes(file, nodes):
    fh = open(file, "w")
    for node in nodes:
        print(nodes[node], file=fh)


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
    res = subprocess.run(
        [
            "fxtran",
            filename,
            "-construct-tag",
            "-no-include",
            "-line-length",
            "9999",
            "-o",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        sys.exit(f"Error when parsing {filename} with fxtran")

    return res.stdout


def get_proc_name(proc):
    sub = proc.find("./subroutine-stmt/subroutine-N/N/n")
    if sub is not None:
        return sub.text.upper()

    func = proc.find("./function-stmt/function-N/N/n")
    if func is not None:
        return func.text.upper()

    prog = proc.find("./program-stmt/program-N/N/n")
    if prog is not None:
        return prog.text.upper()

    return None


def analyze_file(filename, nodes, to_excludes):
    if verbose:
        print("Working on ", filename)

    xml = fxtran_process_file(filename)
    xml = xml.replace('xmlns="http://fxtran.net/#syntax"', "")
    procs = get_procs(xml)
    get_derived_type_procedures(ET.fromstring(xml), filename)
    for dt in derived_types:
        print(dt)

    for proc in procs:

        proc_name = get_proc_name(proc)
        if not proc_name:
            continue

        if proc_name in to_excludes:
            continue

        varnames = get_derived_type_var(proc)

        node = None
        if proc_name in nodes:
            node = nodes[proc_name]
            node.add_filename(filename)
        else:
            node = Node(proc_name, filename)

        calls = proc.findall(".//call-stmt")
        for call in calls:
            callee_name = None
            # the 'cpp' node might be added by a macro between 'N' and 'n' (see call abor1 in bator_pool_balance_mod.F90)
            callee_name = call.find(".//procedure-designator/named-E/N//n").text.upper()

            # sometimes the procedure is member of a type, the name is then not in the <n> tag but in the last <cat> tag of the call
            ct_nodes = call.findall(".//procedure-designator//ct")

            if ct_nodes:
                mc = MethodCall(varnames[callee_name])
                for ct in ct_nodes:
                    mc.cts.append(ct.text.upper())
                node.method_callees.append(mc)
            else:
                if callee_name in to_excludes:
                    continue
                node.add_callee(callee_name)

        nodes[proc_name] = node


def generate_dotfile(nodes, dotfile):
    def build_label(node):
        label = node
        for filename in sorted(nodes[node].filename):
            label += "\\n" + filename.name
        return label

    lines = []
    for node in nodes:
        if nodes[node].hide:
            continue

        node_color = ""
        label = build_label(node)
        if nodes[node].drhook:
            node_color = 'fillcolor="#f7c93d"'

        lines.append(f'{node}[label="{label}"{node_color}];\n')
        for callee in nodes[node].callees:
            lines.append(f"{node} -> {callee};\n")

            # If the nodes is marked hidden but we are still pointing to it,
            # then we can also add the source file
            if callee in nodes and nodes[callee].hide:
                label = build_label(callee)
                line.append(f'{callee}[label="{label}"];\n')

    lines = sorted(lines)
    fh = open(dotfile, "w")
    fh.write("digraph G{\n")
    fh.write("node [shape=box, style=filled];\n")
    fh.writelines(lines)
    fh.write("}\n")


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


def solve_method_calls(nodes):
    for node in nodes:
        for method_call in nodes[node].method_callees:
            if method_call.type not in derived_types:
                nodes[node].add_callee(modethod_call.cts[-1])
                continue
            type_procs = derived_types[method_call.type]
            for proc in type_procs.procedures:
                if proc.alias == method_call.cts[0]:
                    nodes[node].add_callee(proc.name)


def work_on_dir(dirname, to_excludes):
    root = Path(dirname)
    nodes = {}
    for filename in root.glob("**/*.F90"):
        analyze_file(filename, nodes, to_excludes)
        for node in nodes:
            print(type(node), node)

    solve_method_calls(nodes)

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
        print(drhook, "subroutines has been seen in the drhook.prof.* files")

        only_drhook = set()
        for sub in read_drhook(drhook_path):
            if sub not in nodes:
                only_drhook.add(sub)
        print(
            len(only_drhook),
            "subroutines were in drhook.prof.* but not in analyzed source files",
        )
        if len(only_drhook) > 0:
            fh = tempfile.NamedTemporaryFile(delete=False, mode="w")
            for sub in only_drhook:
                fh.write(f"{sub}\n")
            print(f"List saved in {fh.name}")

        print("Total number of routines in drhook:", drhook + len(only_drhook))


def read_drhook(drhook_prof_dir):
    called = set()
    all_drhook_prof = Path(drhook_prof_dir).glob("drhook.prof.*")
    for drhook_prof in all_drhook_prof:
        fh = open(drhook_prof, "r")
        for line in fh.readlines()[16:]:
            # Extract the last column
            line = str(line.split()[-1])

            # Sometimes there is the name of the module in front of the sub name
            line = line.split(":")[-1]

            # Remove the thread number
            line = line.split("@")[0]

            # final cleaning
            line = line.replace("*", "")
            line = line.replace('"', "")
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
    for node in nodes:
        if node in called:
            nodes[node].drhook = True
        else:
            nodes[node].hide = True


def remove_if_not_in_drhook(nodes, called):
    for node in nodes:
        if node in called:
            nodes[node].drhook = True
        else:
            nodes[node].hide = True

    for node in nodes:
        callees = set()
        for call in nodes[node].callees:
            if call in nodes and not nodes[call].hide:
                callees.add(call)
        nodes[node].callees = callees


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
        help="only display subroutines called from this subroutine or one of its callee",
        metavar="SUBROUTINE_NAME",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--dot", help="name of the generated dotfile", metavar="FILE", default="g.dot"
    )
    parser.add_argument("--db", action="store_true")
    parser.add_argument(
        "-s",
        "--stats",
        action="store_true",
        help="display some statistics after the execution",
    )
    parser.add_argument(
        "-e",
        "--excludes",
        help="file containing on each line a subroutine's name to exclude from the graph",
        metavar="FILE",
    )
    parser.add_argument(
        "-k",
        "--known",
        help="only display the calls to subroutines that are known from the parsed code",
        action="store_true",
    )
    parser.add_argument(
        "--nounused",
        help="remove the procedures that are neither called nor calling something",
        action="store_true",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--drhook",
        help="highlights the subroutines that are also in the drhook_prof.* files",
        metavar="DIRECTORY",
    )
    group.add_argument(
        "--drhookonly",
        help="display only the subroutines that are in the parsed codebase and in the drhook_prof.* files",
        metavar="DIRECTORY",
    )
    group.add_argument(
        "--drhookcallees",
        help="display the subroutines that are in the parsed codebase and in the drhook_prof.* files and the callees of those subroutines",
        metavar="DIRECTORY",
    )
    args = parser.parse_args()
    return args


def main():
    if sys.version_info < (3, 10):
        sys.exit("Python is too old, at least python 3.10 is required")

    args = handle_cli_options()
    nodes = {}
    to_excludes = set()

    if not shutil.which("fxtran"):
        sys.exit("The fxtran command needs to be in the PATH")

    if args.verbose:
        global verbose
        verbose = True

    if args.excludes:
        to_excludes = read_excludes_list(args.excludes)

    if args.directory:
        nodes = work_on_dir(args.directory, to_excludes)
    elif args.pack:
        nodes = work_on_pack(args.pack, to_excludes)
    else:
        sys.exit("No pack or directory was specified")

    if args.known:
        nodes = keep_known(nodes)

    if args.nounused:
        nodes = nounused(nodes)

    if args.cutfrom:
        nodes = cut_before(args.cutfrom, nodes)
        if not nodes:
            sys.exit("Couldn't find subroutine {args.cutfrom} in the analyzed files")

    drhook_path = ""
    if args.drhook:
        drhook_path = args.drhook
        called = read_drhook(drhook_path)
        mark_as_seen_in_drhook(nodes, called)

    if args.drhookcallees:
        drhook_path = args.drhookcallees
        called = read_drhook(drhook_path)
        remove_if_not_in_drhook_and_callees(nodes, called)

    if args.drhookonly:
        drhook_path = args.drhookonly
        called = read_drhook(drhook_path)
        remove_if_not_in_drhook(nodes, called)

    if args.dot:
        generate_dotfile(nodes, args.dot)
    if args.db:
        generate_sqlite(nodes)

    if args.stats:
        stats(nodes, drhook_path)


main()
