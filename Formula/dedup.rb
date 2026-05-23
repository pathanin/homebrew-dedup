class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/archive/refs/tags/v0.1.3.tar.gz"
  sha256 "d239f00c946849b835f8fdf74c6a83aff248dd281d1a9b71c3ae7d454fa3a7f9"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install "dedup.py"

    (bin/"dedup").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" -B "#{libexec}/dedup.py" "$@"
    EOS
  end

  test do
    system bin/"dedup", "--help"
  end
end