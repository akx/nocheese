import ast
import os
import random
import re
import requests
import subprocess
import tarfile
import zipfile

link_re = re.compile(r'href="(.+?)"')
pkg_name_re = re.compile("^[^<> =]+")

root_path = os.path.realpath("./root")

def get_pypi_host():
	return "http://pypi.python.org"

def make_dir_for(filename):
	filename = os.path.abspath(filename)
	dirname = os.path.dirname(filename)
	if not os.path.isdir(dirname):
		os.makedirs(dirname)
		return True

def read_requirements(filename):
	print filename
	setup_py = None
	if ".tar." in filename:
		with tarfile.open(filename, "r:*") as tar:
			for member in tar.getmembers():
				if member.name.lower().endswith("setup.py"):
					setup_py = tar.extractfile(member).read()
					break
	elif filename.endswith(".egg"):
		with zipfile.ZipFile(filename, "r") as zip:
			setup_py = zip.read("setup.py")
	else:
		raise NotImplementedError("Not implemented: %s" % filename)

	requirements = set()

	if setup_py:
		tree = ast.parse(setup_py)
		for node in ast.walk(tree):
			arg = getattr(node, "arg", None)
			if arg in ("requires", "install_requires"):
				for node in ast.walk(node.value):
					if isinstance(node, ast.Str):
						requirements.add(node.s)
	
	return requirements

def process_package(package):
	print "Processing package %s" % package
	pypi_host = get_pypi_host()
	resp = requests.get("%s/simple/%s" % (pypi_host, package))
	resp.raise_for_status()
	data = resp.content

	loc_pkg_prefix = "../../packages/"
	pkg_orig_index_path = os.path.join(root_path, "simple", package, "index-orig.html")
	pkg_index_path = os.path.join(root_path, "simple", package, "index.html")
	make_dir_for(pkg_index_path)
	with file(pkg_orig_index_path, "wb") as out_f:
		out_f.write(data.encode("UTF-8"))

	write_in_index = []

	for link in link_re.finditer(data):
		url = link.group(1)
		if url.startswith(loc_pkg_prefix):
			if "/source/" not in url:
				continue
			if "-alpha-" in url:
				continue
			write_in_index.append(url)

	write_in_index.sort()

	all_requirements = set()

	for url in write_in_index:
		in_pkg_url = url.replace(loc_pkg_prefix, "")
		dest_path = os.path.join(root_path, "packages", in_pkg_url).split("#")[0]
		if not os.path.exists(dest_path):
			print "Downloading:"
			url = "%s/packages/%s" % (pypi_host, in_pkg_url)
			print "  <- from -- ", url
			print "  --  to --> ", dest_path
			make_dir_for(dest_path)
			subprocess.check_call(
				[
					"curl",
					"-f",
					"-o",
					dest_path,
					url
				]
			)

		try:
			all_requirements |= read_requirements(dest_path)
		except:
			print "Oops:", dest_path

	with file(pkg_index_path, "wb") as out_f:
		for url in write_in_index:
			out_f.write("<a href=\"%s\">%s</a>\n" % (url, os.path.basename(url)))

	return set(pkg_name_re.search(req).group(0) for req in all_requirements)


class Mirrorator(object):
	def __init__(self, packages):
		self.queue = list(packages)
		self.seen = set()

	def go(self):
		while self.queue:
			package = self.queue.pop(0)
			if package.lower() in self.seen:
				continue
			self.seen.add(package.lower())
			requirements = process_package(package)
			if requirements:
				print "Adding new requirements: %s" % sorted(requirements)
				self.queue.extend(requirements)

if __name__ == '__main__':
	packages = set()
	with file("packages.txt", "rb") as packages_file:
		for line in packages_file:
			packages.add(line.strip())

	m = Mirrorator(sorted(packages))
	m.go()