class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin/homebrew-dedup"
  url "https://github.com/pathanin/homebrew-dedup/archive/refs/tags/v0.1.2.tar.gz"
  sha256 "13c1c5326ac29f04e4cde460c52b2ce62018ff98ac283611d71d2a1e7bc34694"
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