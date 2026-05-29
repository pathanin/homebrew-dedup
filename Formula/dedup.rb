class Dedup < Formula
  include Language::Python::Virtualenv

  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/releases/download/v0.2.2/homebrew-dedup-0.2.2.tar.gz"
  sha256 "216d911a7bdd779073d903370fcf307a44dda2ead6d1851231a7c5f18e1a8410"
  license "MIT"

  depends_on "python@3.12"

  resource "send2trash" do
    url "https://files.pythonhosted.org/packages/c5/f0/184b4b5f8d00f2a92cf96eec8967a3d550b52cf94362dad1100df9e48d57/send2trash-2.1.0.tar.gz"
    sha256 "1c72b39f09457db3c05ce1d19158c2cbef4c32b8bedd02c155e49282b7ea7459"
  end

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install resource("send2trash")

    libexec.install "dedup.py"

    (bin/"dedup").write <<~EOS
      #!/bin/bash
      exec "#{libexec}/bin/python" -B "#{libexec}/dedup.py" "$@"
    EOS
  end

  test do
    system libexec/"bin/python", "-c", "import send2trash"
    system bin/"dedup", "--help"
  end
end
