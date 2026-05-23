class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "672adce744624da10134deccc3dbcea995b2bd82d8c9fae13210e290ed3d91a9"
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