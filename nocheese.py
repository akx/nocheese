import ast
import codecs
import os
import random
import re
import requests
import subprocess
import tarfile
import time
import zipfile

def flatten(s):
	return "".join([c for c in s.lower().encode("ascii", "ignore") if c.isalnum()])

link_re = re.compile(r'href="(.+?)"')
pkg_name_re = re.compile("^[^<> =]+")
simple_index_pkg_name_re = re.compile(r"'>(.+?)</a")
package_aliases = {}
root_path = os.path.realpath("./root")

def get_pypi_host():
	return "http://b.pypi.python.org"

def make_dir_for(filename):
	filename = os.path.abspath(filename)
	dirname = os.path.dirname(filename)
	if not os.path.isdir(dirname):
		os.makedirs(dirname)
		return True

def read_package_name(package_name):
	return pkg_name_re.match(package_name).group(0)

def read_setup_py(setup_py):
	requirements = set()
	tree = ast.parse(setup_py)
	for node in ast.walk(tree):
		arg = getattr(node, "arg", None)
		if arg in ("requires", "install_requires"):
			for node in ast.walk(node.value):
				if isinstance(node, ast.Str):
					requirements.add(read_package_name(node.s))
	return requirements

def read_requirements_txt(requirements_txt):
	requirements = set()
	for line in requirements_txt.splitlines():
		line = line.strip()
		if line and not line.startswith("#"):
			requirements.add(read_package_name(line.strip()))
	return requirements

class ICompressed(object):
	def __init__(self, filename):
		self.obj = None
		raise NotImplementedError("Not implemented")
	def __iter__(self):
		raise NotImplementedError("Not implemented")
	def read(self, member):
		raise NotImplementedError("Not implemented")
	def __enter__(self):
		return self
	def __exit__(self, *args):
		self.obj.close()

class Tar(ICompressed):
	def __init__(self, filename):
		self.obj = tarfile.open(filename, "r:*")
	
	def __iter__(self):
		for member in self.obj:
			yield member

	def read(self, member):
		return self.obj.extractfile(member).read()

class Zip(ICompressed):
	def __init__(self, filename):
		self.obj = zipfile.ZipFile(filename, "r")

	def __iter__(self):
		for member in self.obj.infolist():
			yield member

	def read(self, member):
		return self.obj.read(member)



def read_requirements(filename):
	requirements = set()
	setup_py = None
	if ".tar." in filename or filename.endswith(".tgz"):
		comp = Tar(filename)
	elif filename.endswith(".egg") or filename.endswith(".zip"):
		comp = Zip(filename)
	else:
		raise NotImplementedError("Not implemented: %s" % filename)

	for member in comp:
		name = getattr(member, "name", getattr(member, "filename", "")).lower()
		if name.endswith("setup.py"):
			requirements |= read_setup_py(comp.read(member))
		elif name.endswith("requirements.txt"):
			requirements |= read_requirements_txt(comp.read(member))
	
	return requirements



class Mirrorator(object):
	def __init__(self, packages):
		self.queue = list(packages)
		self.seen = set()
		self.all_urls = []
		self.requirement_tree = {}

	def process_package(self, package):
		print "Processing package %s" % package
		pypi_host = get_pypi_host()
		resp = requests.get("%s/simple/%s/" % (pypi_host, package))
		if resp.status_code == 404:
			print " --- Package index unavailable: %s" % package
			return

		resp.raise_for_status()
		data = resp.content

		loc_pkg_prefix = "../../packages/"
		loc_pkg_prefix_rewrite = "packages/"

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
				url = url.replace(loc_pkg_prefix, loc_pkg_prefix_rewrite).split("#")[0]
				write_in_index.append(url)

		write_in_index.sort()

		all_requirements = set()

		for url in write_in_index:
			
			dest_path = os.path.join(root_path, url)
			if not os.path.exists(dest_path):
				print "Downloading:"
				url = "%s/%s" % (pypi_host, url)
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
			else:
				print "Skipping download of %s" % url
			try:
				all_requirements |= read_requirements(dest_path)
			except KeyboardInterrupt:
				raise
			except Exception, exc:
				print "Failed reading requirements from %r" % dest_path
				print "  (%s)" % exc

		# Write /simple/<package>/index.html

		with file(pkg_index_path, "wb") as out_f:
			for url in write_in_index:
				out_f.write("<a href=\"/%s\">%s</a>\n" % (url, os.path.basename(url)))
				self.all_urls.append((package, url))

		all_requirements = set(pkg_name_re.search(req).group(0) for req in all_requirements)
		return all_requirements

	def write_all_index(self):
		all_index_path = os.path.join(root_path, "index.html")
		with file(all_index_path, "wb") as out_f:
			for package, url in self.all_urls:
				out_f.write("%s: <a href=\"%s\">%s</a><br>" % (package, url, os.path.basename(url)))

	def write_simple_index(self):
		s_index_path = os.path.join(root_path, "simple", "index.html")
		make_dir_for(s_index_path)
		with file(s_index_path, "wb") as out_f:
			packages = set(package for (package, url) in self.all_urls)
			for package in packages:
				out_f.write("<a href=\"/simple/%s/\">%s</a><br>" % (package, package))


	def go(self):
		while self.queue:
			package = self.queue.pop(0)
			flatpack = flatten(package)
			if flatpack in self.seen:
				continue
			self.seen.add(flatpack)
			
			package = package_aliases.get(flatpack, package)
			requirements = self.process_package(package)
			if requirements:
				requirements = set([package_aliases.get(r, r) for r in [flatten(r) for r in requirements]])
				self.requirement_tree[package] = requirements
				print "Adding new requirements from %s: %s" % (package, sorted(requirements))
				self.queue.extend(requirements)

		self.write_all_index()
		self.write_simple_index()

		with file("requirement-tree.txt", "wb") as outf:
			for package, requirements in sorted(self.requirement_tree.iteritems()):
				outf.write("%s <- %s\n" % (package, ", ".join(requirements)))



def download_package_aliases():
	pypi_host = get_pypi_host()
	data = requests.get("%s/simple/" % pypi_host).text
	packages = sorted(m.group(1) for m in simple_index_pkg_name_re.finditer(data))
	with codecs.open("package-index.txt", "wb", "UTF-8") as outf:
		for package in packages:
			outf.write(package.strip() + "\n")#print >>outf, package

def read_package_aliases():
	aliases = {}
	with codecs.open("package-index.txt", "rb", "UTF-8") as inf:
		for line in inf:
			line = line.strip()
			flat = flatten(line)
			if flat:
				aliases[flat] = line
	return aliases

def check_aliases():
	if not os.path.isfile("package-index.txt") or time.time() - os.stat("package-index.txt").st_mtime > 86400 * 3.5:
		print "Downloading package index..."
		download_package_aliases()

	package_aliases.update(read_package_aliases())
	print "Read %d packages from index" % len(package_aliases)	

def main():
	check_aliases()

	packages = set()
	with file("packages.txt", "rb") as packages_file:
		for line in packages_file:
			line = line.strip()
			if line and not line.startswith("#"):
				packages.add(line)

	m = Mirrorator(sorted(packages))
	m.go()

if __name__ == '__main__':
	main()