import xml.etree.ElementTree as ET
import sys
import pyfxtran
from pathlib import Path

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


root = Path(sys.argv[1])
for filename in root.glob("**/*.F90"):
    print("Working on ", filename)
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
        calls = proc.findall(".//call-stmt")
        callees = []
        for call in calls:
            callees.append(call.find("procedure-designator/named-E/N/n").text)
        # print(proc.find(f'./end-subroutine-stmt/subroutine-N/N/n').text)
        print(f"{proc_name}:")
        for c in callees:
            print("\t", c)
